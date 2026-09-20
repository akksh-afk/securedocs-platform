require("dotenv").config();

// ---------------------------------------------------------------
// Create the WORM bucket (F6).
//
//   node scripts/init-minio.js
//
// Object Lock is what makes "WORM object store" true rather than a
// claim on a slide. It must be enabled AT CREATION - S3 and MinIO both
// refuse to turn it on for a bucket that already exists, so a bucket
// made by hand without it has to be recreated.
//
// GOVERNANCE mode, not COMPLIANCE: governance can be overridden by a
// user holding s3:BypassGovernanceRetention, which is what you want
// while developing. COMPLIANCE cannot be overridden by anyone, root
// included, until the retention period expires - correct for
// production, ruinous on a laptop the week before a deadline.
// ---------------------------------------------------------------

const {
    S3Client,
    CreateBucketCommand,
    PutObjectLockConfigurationCommand,
    HeadBucketCommand,
} = require("@aws-sdk/client-s3");

const BUCKET = process.env.MINIO_BUCKET || "securedocs";
const RETAIN_DAYS = Number(process.env.MINIO_RETAIN_DAYS || 3650);

const s3 = new S3Client({
    endpoint: process.env.MINIO_ENDPOINT || "http://localhost:9000",
    region: process.env.MINIO_REGION || "us-east-1",
    forcePathStyle: true,
    credentials: {
        accessKeyId: process.env.MINIO_ACCESS_KEY || "minioadmin",
        secretAccessKey: process.env.MINIO_SECRET_KEY || "minioadmin",
    },
});

(async () => {
    try {
        await s3.send(new HeadBucketCommand({ Bucket: BUCKET }));
        console.log(`Bucket "${BUCKET}" already exists.`);
        console.log(
            "If it was created without Object Lock it is NOT a WORM store - " +
            "delete it and re-run this script."
        );
    } catch {
        await s3.send(
            new CreateBucketCommand({
                Bucket: BUCKET,
                ObjectLockEnabledForBucket: true,
            })
        );
        console.log(`Created bucket "${BUCKET}" with Object Lock enabled.`);
    }

    await s3.send(
        new PutObjectLockConfigurationCommand({
            Bucket: BUCKET,
            ObjectLockConfiguration: {
                ObjectLockEnabled: "Enabled",
                Rule: {
                    DefaultRetention: {
                        Mode: "GOVERNANCE",
                        Days: RETAIN_DAYS,
                    },
                },
            },
        })
    );

    console.log(`Default retention: GOVERNANCE, ${RETAIN_DAYS} days.`);
    console.log("Overwriting or deleting an object is now refused by the store.");
})().catch((err) => {
    console.error("MinIO setup failed:", err.message);
    console.error("Is MinIO running? docker run -p 9000:9000 -p 9001:9001 minio/minio server /data --console-address :9001");
    process.exit(1);
});
