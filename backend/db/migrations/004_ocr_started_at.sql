-- Records when the reader picked a document up.
--
-- Run with:  npm run db:setup
--
-- Without this there is no way to tell a document that is genuinely
-- being read from one whose reader died halfway through. A killed
-- worker left its rows sitting in 'processing' forever: the queue only
-- ever claims 'pending', so nothing went back for them and the document
-- was never read, with no error to show for it.
--
-- With a claim time, a reaper can return anything that has been held
-- too long. Using a timestamp rather than simply resetting everything
-- at startup is what keeps it safe to run more than one reader - a
-- fresh worker must not steal a document another one is midway through.

ALTER TABLE document_versions
  ADD COLUMN IF NOT EXISTS ocr_started_at TIMESTAMPTZ;

-- Anything already stuck predates this column, so it has no claim time
-- and would never be reaped. Release it now.
UPDATE document_versions
   SET ocr_status = 'pending'
 WHERE ocr_status = 'processing'
   AND ocr_started_at IS NULL;
