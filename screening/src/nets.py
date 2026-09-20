"""Network definitions shared by training and runtime inference.

All models are fully convolutional. Runtime loads ``models/<name>.onnx`` through OpenCV DNN
when present (no PyTorch needed), otherwise ``models/<name>.pt`` through PyTorch."""
from __future__ import annotations

try:
    import torch
    from torch import nn
    import torch.nn.functional as F
except ImportError:  # runtime can still use ONNX models through OpenCV
    torch = None

    class _Missing:
        Module = object

        def __getattr__(self, name):
            raise ImportError(TORCH_HINT)

    nn = _Missing()
    F = _Missing()

TORCH_HINT = ('PyTorch is not installed in this Python environment, so the trained networks cannot run. '
              'Install it with "pip install torch" (or the CUDA build for a GPU), or export the models to ONNX.')


def cbr(cin, cout, k=3, s=1, p=None, d=1):
    if p is None:
        p = (k // 2) * d if isinstance(k, int) else tuple((kk // 2) * d for kk in k)
    return nn.Sequential(nn.Conv2d(cin, cout, k, s, p, dilation=d, bias=False), nn.BatchNorm2d(cout), nn.ReLU(inplace=True))


class _Res1D(nn.Module):
    def __init__(self, ch, dilation):
        super().__init__()
        self.body = nn.Sequential(
            nn.Conv2d(ch, ch, (1, 5), padding=(0, 2 * dilation), dilation=(1, dilation), bias=False), nn.BatchNorm2d(ch),
            nn.ReLU(inplace=True), nn.Conv2d(ch, ch, (1, 3), padding=(0, 1), bias=False), nn.BatchNorm2d(ch))

    def forward(self, x):
        return F.relu(x + self.body(x))


class MRZNet(nn.Module):
    """32x512 grey line -> (N, classes, 1, 128) CTC logits. Class 0 is the CTC blank."""

    def __init__(self, num_classes: int = 38, width: int = 256):
        super().__init__()
        self.features = nn.Sequential(
            cbr(1, 32), cbr(32, 32), nn.MaxPool2d(2),                 # 16x256
            cbr(32, 64), cbr(64, 64), nn.MaxPool2d(2),                # 8x128
            cbr(64, 128), cbr(128, 128), nn.MaxPool2d((2, 1)),        # 4x128
            cbr(128, 192), cbr(192, 192),
            cbr(192, width, k=(4, 1), p=(0, 0)),                      # 1x128
        )
        self.context = nn.Sequential(_Res1D(width, 1), _Res1D(width, 2), _Res1D(width, 4), _Res1D(width, 1))
        self.head = nn.Sequential(nn.Dropout(0.1), nn.Conv2d(width, num_classes, 1))

    def forward(self, x):
        return self.head(self.context(self.features(x)))


class _SRM(nn.Module):
    """Fixed steganalysis-rich-model high-pass filters: expose noise residuals where
    pasted or re-rendered regions break the natural noise pattern."""

    def __init__(self):
        super().__init__()
        f1 = torch.tensor([[0, 0, 0, 0, 0], [0, -1, 2, -1, 0], [0, 2, -4, 2, 0], [0, -1, 2, -1, 0], [0, 0, 0, 0, 0]], dtype=torch.float32) / 4
        f2 = torch.tensor([[-1, 2, -2, 2, -1], [2, -6, 8, -6, 2], [-2, 8, -12, 8, -2], [2, -6, 8, -6, 2], [-1, 2, -2, 2, -1]], dtype=torch.float32) / 12
        f3 = torch.tensor([[0, 0, 0, 0, 0], [0, 0, 0, 0, 0], [0, 1, -2, 1, 0], [0, 0, 0, 0, 0], [0, 0, 0, 0, 0]], dtype=torch.float32) / 2
        w = torch.stack([f1, f2, f3])[:, None].repeat(1, 1, 1, 1)
        self.register_buffer('weight', w)

    def forward(self, gray):
        return torch.clamp(F.conv2d(gray, self.weight, padding=2) * 4.0, -3, 3)


class _Down(nn.Module):
    def __init__(self, cin, cout):
        super().__init__()
        self.body = nn.Sequential(cbr(cin, cout, s=2), cbr(cout, cout))

    def forward(self, x):
        return self.body(x)


class _Up(nn.Module):
    def __init__(self, cin, cskip, cout):
        super().__init__()
        self.body = nn.Sequential(cbr(cin + cskip, cout), cbr(cout, cout))

    def forward(self, x, skip):
        x = F.interpolate(x, scale_factor=2.0, mode='nearest')
        return self.body(torch.cat([x, skip], 1))


class SegUNet(nn.Module):
    """Compact U-Net. Input is RGB in [0,1] (N,3,H,W) with H, W divisible by 32.

    ``forensic=True`` adds SRM noise-residual channels computed inside the network
    (tamper localisation); otherwise it is a plain layout segmenter."""

    def __init__(self, out_channels: int, base: int = 24, forensic: bool = False):
        super().__init__()
        self.forensic = forensic
        cin = 3 + (3 if forensic else 0)
        if forensic:
            self.srm = _SRM()
        b = base
        self.stem = nn.Sequential(cbr(cin, b), cbr(b, b))
        self.d1, self.d2, self.d3, self.d4, self.d5 = _Down(b, b * 2), _Down(b * 2, b * 4), _Down(b * 4, b * 6), _Down(b * 6, b * 8), _Down(b * 8, b * 8)
        self.u4, self.u3, self.u2, self.u1, self.u0 = _Up(b * 8, b * 8, b * 6), _Up(b * 6, b * 6, b * 4), _Up(b * 4, b * 4, b * 2), _Up(b * 2, b * 2, b), _Up(b, b, b)
        self.head = nn.Conv2d(b, out_channels, 1)

    def forward(self, x):
        if self.forensic:
            gray = (x[:, 0:1] * 0.114 + x[:, 1:2] * 0.587 + x[:, 2:3] * 0.299) * 255.0
            x = torch.cat([x, self.srm(gray)], 1)
        s0 = self.stem(x)
        s1 = self.d1(s0); s2 = self.d2(s1); s3 = self.d3(s2); s4 = self.d4(s3); s5 = self.d5(s4)
        y = self.u4(s5, s4); y = self.u3(y, s3); y = self.u2(y, s2); y = self.u1(y, s1); y = self.u0(y, s0)
        return self.head(y)


# ---------------------------------------------------------------------------
# Runtime loading
# ---------------------------------------------------------------------------

from pathlib import Path as _Path

MODEL_DIR = _Path(__file__).resolve().parents[1] / 'models'
_FACTORIES = {
    'mrz_reader': lambda: MRZNet(),
    'layout': lambda: SegUNet(3, base=16),
    'tamper': lambda: SegUNet(1, base=24, forensic=True),
}


class Predictor:
    """Callable ``(N,C,H,W) float32 ndarray -> ndarray`` backed by ONNX/OpenCV or PyTorch."""

    def __init__(self, name: str, model_dir: _Path = MODEL_DIR, prefer: str = 'auto'):
        import numpy as np  # noqa: F401  (kept local so importing nets stays cheap)
        self.name = name
        onnx_path, pt_path = model_dir / f'{name}.onnx', model_dir / f'{name}.pt'
        self.backend = None
        if prefer in ('auto', 'onnx') and onnx_path.exists():
            import cv2
            self.net = cv2.dnn.readNetFromONNX(str(onnx_path))
            self.backend = 'opencv-onnx'
        elif pt_path.exists():
            if torch is None:
                raise ImportError(TORCH_HINT)
            self.torch = torch
            self.device = torch.device('cuda' if torch.cuda.is_available() and prefer != 'cpu' else 'cpu')
            self.model = _FACTORIES[name]()
            self.model.load_state_dict(torch.load(pt_path, map_location=self.device))
            self.model.to(self.device).eval()
            self.backend = f'torch-{self.device.type}'
        else:
            raise FileNotFoundError(f'No trained weights for {name!r} in {model_dir}')

    def __call__(self, x):
        if self.backend == 'opencv-onnx':
            self.net.setInput(x)
            return self.net.forward()
        with torch.no_grad():
            t = torch.from_numpy(x).to(self.device)
            return self.model(t).float().cpu().numpy()
