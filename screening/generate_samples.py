from __future__ import annotations
from pathlib import Path
import os, random, json
import cv2, numpy as np
from PIL import Image, ImageDraw, ImageFont
from src.mrz_utils import generate_td3_mrz

ROOT=Path(__file__).parent
OUT=ROOT/'samples'/'generated'
for p in OUT.glob('*.png'): p.unlink()
OUT.mkdir(parents=True,exist_ok=True)
random.seed(42)
rng=np.random.default_rng(42)

WIN=Path(os.environ.get('WINDIR',r'C:\\Windows'))/'Fonts'
font_candidates=[WIN/'arial.ttf',WIN/'segoeui.ttf',Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
mono_candidates=[WIN/'consola.ttf',WIN/'cour.ttf',Path('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf')]
def pick(cands):
    for p in cands:
        if p.exists(): return str(p)
    return None
FONT=pick(font_candidates); MONO=pick(mono_candidates) or FONT
def f(size,mono=False):
    p=MONO if mono else FONT
    return ImageFont.truetype(p,size) if p else ImageFont.load_default()

RECORDS=[
 dict(surname='GUPTA',given_names='AKSHIT',passport_number='K71M4P829',nationality='UTO',date_of_birth='020214',sex='M',date_of_expiry='350918',dob='14 FEB 2002',exp='18 SEP 2035'),
 dict(surname='SINGH',given_names='RHEA ANIKA',passport_number='P93Z6N417',nationality='UTO',date_of_birth='990708',sex='F',date_of_expiry='341201',dob='08 JUL 1999',exp='01 DEC 2034'),
 dict(surname='MEHTA',given_names='VIKRAM',passport_number='L5Q8D2173',nationality='UTO',date_of_birth='850923',sex='M',date_of_expiry='301106',dob='23 SEP 1985',exp='06 NOV 2030'),
 dict(surname='KHAN',given_names='SANA',passport_number='A64X8R205',nationality='UTO',date_of_birth='970331',sex='F',date_of_expiry='290622',dob='31 MAR 1997',exp='22 JUN 2029'),
 dict(surname='SHARMA',given_names='DEV RAJ',passport_number='N82B7K564',nationality='UTO',date_of_birth='910411',sex='M',date_of_expiry='330909',dob='11 APR 1991',exp='09 SEP 2033'),
 dict(surname='PARKER',given_names='ELENA',passport_number='T49C2M781',nationality='UTO',date_of_birth='001129',sex='F',date_of_expiry='361230',dob='29 NOV 2000',exp='30 DEC 2036'),
]

def base_identity(r,w=1800,h=1150):
    im=Image.new('RGB',(w,h),(238,235,220)); d=ImageDraw.Draw(im)
    d.rectangle((22,22,w-22,h-22),outline=(35,35,35),width=5)
    d.text((70,45),'SYNTHETIC TRAVEL DOCUMENT - SIH TEST ONLY',font=f(42),fill=(20,20,20))
    d.text((70,100),'NOT A REAL GOVERNMENT DOCUMENT',font=f(25),fill=(170,20,20))
    # portrait block
    d.rectangle((70,200,520,700),outline=(80,80,80),width=4)
    d.ellipse((210,250,380,420),fill=(165,125,105),outline=(40,40,40),width=3)
    d.polygon([(160,650),(195,445),(395,445),(430,650)],fill=(65,83,112),outline=(35,35,35))
    d.text((600,200),'SURNAME',font=f(25),fill=(90,90,90)); d.text((600,238),r['surname'],font=f(38),fill=(25,25,25))
    d.text((600,310),'GIVEN NAMES',font=f(25),fill=(90,90,90)); d.text((600,348),r['given_names'],font=f(38),fill=(25,25,25))
    d.text((600,420),'PASSPORT NO',font=f(25),fill=(90,90,90)); d.text((600,458),r['passport_number'],font=f(38,True),fill=(25,25,25))
    d.text((600,530),'NATIONALITY',font=f(25),fill=(90,90,90)); d.text((600,568),r['nationality'],font=f(38),fill=(25,25,25))
    d.text((600,640),'DATE OF BIRTH',font=f(25),fill=(90,90,90)); d.text((600,678),r['dob'],font=f(34),fill=(25,25,25))
    d.text((600,750),'SEX',font=f(25),fill=(90,90,90)); d.text((600,788),r['sex'],font=f(34),fill=(25,25,25))
    d.text((900,750),'DATE OF EXPIRY',font=f(25),fill=(90,90,90)); d.text((900,788),r['exp'],font=f(34),fill=(25,25,25))
    mrz=generate_td3_mrz(passport_number=r['passport_number'],nationality=r['nationality'],date_of_birth=r['date_of_birth'],sex=r['sex'],date_of_expiry=r['date_of_expiry'],surname=r['surname'],given_names=r['given_names'],issuing_state='UTO')
    d.rectangle((50,860,w-50,1080),fill=(220,218,205),outline=(90,90,90),width=2)
    d.text((75,880),mrz[0],font=f(44,True),fill=(15,15,15)); d.text((75,965),mrz[1],font=f(44,True),fill=(15,15,15))
    return im, mrz

def cctns_style(seed):
    w,h=1700,1200; im=Image.new('RGB',(w,h),(247,245,238)); d=ImageDraw.Draw(im)
    d.rectangle((20,20,w-20,h-20),outline=(30,30,30),width=4)
    d.text((60,45),'SYNTHETIC POLICE CASE RECORD / CCTNS-STYLE',font=f(40),fill=(20,20,20))
    d.text((60,95),'NOT AN OFFICIAL CCTNS RECORD - TEST DATA ONLY',font=f(24),fill=(180,25,25))
    fields=[('CASE ID',f'CASE-{seed:05d}'),('POLICE STATION','NORTH ZONE TEST UNIT'),('REPORT DATE','16/09/2026'),('INCIDENT TYPE','IDENTITY DOCUMENT SCREENING'),('SUBJECT NAME',['AKSHIT GUPTA','RHEA ANIKA SINGH','DEV RAJ SHARMA'][seed%3]),('DOCUMENT TYPE','PASSPORT / VISA'),('DOCUMENT STATUS','REVIEW REQUIRED'),('OFFICER CODE',f'OFF-{1000+seed}')]
    y=175
    for i,(k,v) in enumerate(fields):
        d.text((70,y),k,font=f(23),fill=(90,90,90)); d.text((410,y),v,font=f(30),fill=(25,25,25)); y+=100 if i<3 else 92
    d.line((70,950,w-70,950),fill=(90,90,90),width=2)
    d.text((70,975),'Observations: OCR / stamp / date consistency check pending.',font=f(26),fill=(30,30,30))
    d.text((70,1022),'Evidence ref: SYNTHETIC-NONPRODUCTION',font=f(23),fill=(30,30,30))
    return im

def tamper(im,kind):
    arr=np.array(im).copy()
    h,w=arr.shape[:2]
    if kind=='text_patch':
        cv2.rectangle(arr,(860,410),(1250,505),(235,230,210),-1); cv2.putText(arr,'X9Q7', (895,470),cv2.FONT_HERSHEY_SIMPLEX,1.2,(20,20,20),3,cv2.LINE_AA)
    elif kind=='photo_patch':
        cv2.rectangle(arr,(85,215),(505,685),(45,45,45),-1)
    elif kind=='stamp_clone':
        cv2.circle(arr,(1450,285),80,(80,80,150),6); cv2.circle(arr,(1450,285),65,(80,80,150),2)
    elif kind=='date_patch':
        cv2.rectangle(arr,(900,750),(1450,835),(235,230,210),-1); cv2.putText(arr,'31 DEC 2038',(920,805),cv2.FONT_HERSHEY_SIMPLEX,1.05,(15,15,15),2,cv2.LINE_AA)
    return Image.fromarray(arr)

def degrade(im,mode):
    arr=np.array(im)
    h,w=arr.shape[:2]
    if mode=='good': out=arr
    elif mode=='low_light':
        out=cv2.convertScaleAbs(arr,alpha=0.52,beta=-20)
    elif mode=='overexposed': out=cv2.convertScaleAbs(arr,alpha=1.75,beta=45)
    elif mode=='motion_blur':
        k=21; ker=np.zeros((k,k)); ker[k//2,:]=1.0/k; out=cv2.filter2D(arr,-1,ker)
    elif mode=='defocus': out=cv2.GaussianBlur(arr,(0,0),4.0)
    elif mode=='tiny':
        small=cv2.resize(arr,(520,332),interpolation=cv2.INTER_AREA); out=cv2.resize(small,(w,h),interpolation=cv2.INTER_CUBIC)
    elif mode=='perspective':
        p1=np.float32([[0,0],[w,0],[0,h],[w,h]]); p2=np.float32([[120,40],[w-90,80],[40,h-30],[w-140,h-10]]); M=cv2.getPerspectiveTransform(p1,p2); out=cv2.warpPerspective(arr,M,(w,h),borderMode=cv2.BORDER_REPLICATE)
    elif mode=='glare':
        out=arr.copy(); cx,cy=int(w*.72),int(h*.52); yy,xx=np.ogrid[:h,:w]; dist=((xx-cx)**2+(yy-cy)**2)**0.5; alpha=np.clip(1-dist/300,0,1)*210; out=np.clip(out.astype(float)+alpha[...,None],0,255).astype(np.uint8)
    elif mode=='noise':
        n=rng.normal(0,22,arr.shape); out=np.clip(arr.astype(float)+n,0,255).astype(np.uint8)
    elif mode=='jpeg':
        small=cv2.resize(arr,(900,575),interpolation=cv2.INTER_AREA); ok,enc=cv2.imencode('.jpg',small,[cv2.IMWRITE_JPEG_QUALITY,22]); out=cv2.imdecode(enc,cv2.IMREAD_COLOR); out=cv2.resize(out,(w,h),interpolation=cv2.INTER_CUBIC)
    elif mode=='shadow':
        out=arr.copy(); poly=np.array([[(0,0),(w,0),(int(w*.55),h),(0,h)]],np.int32); mask=np.zeros((h,w),np.uint8); cv2.fillPoly(mask,poly,150); out=cv2.addWeighted(out,1.0,cv2.cvtColor(mask,cv2.COLOR_GRAY2BGR),-0.7,60)
    elif mode=='partial':
        out=arr.copy(); cv2.rectangle(out,(0,int(h*.78)),(w,h),(30,30,30),-1)
    elif mode=='rotate180': out=cv2.rotate(arr,cv2.ROTATE_180)
    elif mode=='combo_worst':
        out=cv2.convertScaleAbs(arr,alpha=.58,beta=-35); out=cv2.GaussianBlur(out,(5,5),2.5); small=cv2.resize(out,(480,306),interpolation=cv2.INTER_AREA); out=cv2.resize(small,(w,h),interpolation=cv2.INTER_CUBIC); n=rng.normal(0,16,out.shape); out=np.clip(out.astype(float)+n,0,255).astype(np.uint8)
    elif mode=='occluded_glare':
        out=arr.copy(); cv2.rectangle(out,(760,610),(1240,820),(115,115,115),-1); cv2.ellipse(out,(1250,430),(330,190),0,0,360,(245,245,245),-1); out=cv2.GaussianBlur(out,(3,3),1.0)
    else: out=arr
    return Image.fromarray(out)

modes=['good','low_light','overexposed','motion_blur','defocus','tiny','perspective','glare','noise','jpeg','shadow','partial','rotate180','combo_worst','occluded_glare']
records=[]
for idx,r in enumerate(RECORDS,1):
    base,mrz=base_identity(r)
    for j,mode in enumerate(modes):
        path=OUT/f'identity_{idx:02d}_{j:02d}_{mode}.jpg'
        degrade(base,mode).save(path,quality=88 if mode in {'jpeg','tiny'} else 95)
        records.append({'file':str(path.relative_to(ROOT)).replace('\\','/'),'family':'identity','capture_condition':mode,'tampered':False,'expected':{'passport_number':r['passport_number'],'date_of_birth':r['date_of_birth'],'date_of_expiry':r['date_of_expiry'],'nationality':r['nationality']}})
    for kind in ['text_patch','photo_patch','stamp_clone','date_patch']:
        t=tamper(base,kind); mode='tamper_'+kind
        for extra in ['good','low_light','glare']:
            p=OUT/f'identity_{idx:02d}_{kind}_{extra}.jpg'; degrade(t,extra).save(p,quality=92)
            records.append({'file':str(p.relative_to(ROOT)).replace('\\','/'),'family':'identity','capture_condition':extra,'tampered':True,'tamper_type':kind,'expected':{'passport_number':r['passport_number'],'date_of_birth':r['date_of_birth'],'date_of_expiry':r['date_of_expiry']}})

for i in range(10):
    base=cctns_style(i+1)
    for j,mode in enumerate(['good','tiny','low_light','motion_blur','perspective','jpeg','glare','combo_worst']):
        p=OUT/f'cctns_style_{i+1:02d}_{j:02d}_{mode}.jpg'; degrade(base,mode).save(p,quality=88)
        records.append({'file':str(p.relative_to(ROOT)).replace('\\','/'),'family':'cctns_style','capture_condition':mode,'tampered':False,'expected':{}})

(ROOT/'samples'/'manifest.json').write_text(json.dumps({'dataset_name':'SIH26188_robust_capture_synthetic_v2','disclaimer':'All images are synthetic test data. CCTNS-style samples are not official CCTNS records; identity documents are not valid government documents.','samples':records},indent=2),encoding='utf-8')
print(f'Generated {len(records)} hard samples in {OUT}')
