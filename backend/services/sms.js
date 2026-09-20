// ---------------------------------------------------------------
// Delivery for sign-in codes.
//
//   SMS_PROVIDER=twilio   real SMS through Twilio's REST API. Needs
//                         TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and
//                         TWILIO_FROM. Plain fetch, no SDK.
//   SMS_PROVIDER=console  development only. The terminal running the
//                         server stands in for the officer's phone.
//                         Refuses to send when NODE_ENV=production, so
//                         a misconfigured deployment fails closed instead
//                         of printing live codes into the host's logs.
//
// There is deliberately NO default. Defaulting to console meant a host
// that never set NODE_ENV - a staging box, a plain `node server.js` -
// printed live sign-in codes into its logs. Choosing where codes go is
// now an explicit decision, and nothing is sent until it is made.
//
// Another provider (MSG91, Gupshup, AWS SNS) is one more function with
// the same (to, body) shape.
// ---------------------------------------------------------------

const PROVIDER = process.env.SMS_PROVIDER || null;

// Officers are registered with Indian numbers; a bare ten-digit number
// is read as one. Anything else must already be full E.164.
const DEFAULT_COUNTRY = "+91";

function normalizeMobile(input) {
    const s = String(input || "").replace(/[\s().-]/g, "");
    const e164 = /^\d{10}$/.test(s) ? DEFAULT_COUNTRY + s : s;
    return /^\+[1-9]\d{7,14}$/.test(e164) ? e164 : null;
}

// For audit detail: enough to tell two numbers apart, not enough to
// dial one.
function maskMobile(e164) {
    return e164 ? e164.slice(0, 3) + "*".repeat(e164.length - 5) + e164.slice(-2) : null;
}

async function twilio(to, body) {
    const sid = process.env.TWILIO_ACCOUNT_SID;
    const res = await fetch(
        `https://api.twilio.com/2010-04-01/Accounts/${sid}/Messages.json`,
        {
            method: "POST",
            headers: {
                Authorization:
                    "Basic " +
                    Buffer.from(`${sid}:${process.env.TWILIO_AUTH_TOKEN}`).toString("base64"),
            },
            body: new URLSearchParams({ To: to, From: process.env.TWILIO_FROM, Body: body }),
        }
    );
    // Status only. The body we sent contains the code, and error text
    // ends up in logs.
    if (!res.ok) throw new Error(`twilio responded ${res.status}`);
}

async function consoleSms(to, body) {
    if (process.env.NODE_ENV === "production") {
        throw new Error("SMS_PROVIDER is not configured for production");
    }
    console.log(`\n  [dev SMS -> ${to}]  ${body}\n`);
}

const providers = { twilio, console: consoleSms };

async function send(to, body) {
    if (!PROVIDER) {
        throw new Error("SMS_PROVIDER is not set (use console in development)");
    }
    const provider = providers[PROVIDER];
    if (!provider) throw new Error(`unknown SMS_PROVIDER "${PROVIDER}"`);
    return provider(to, body);
}

module.exports = { send, normalizeMobile, maskMobile, provider: PROVIDER };
