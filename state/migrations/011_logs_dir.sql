-- Migration 011: Persist workflow logs directory at creation time.
--
-- This enables log lookups and resume/retry writes to remain pinned to the
-- original workflow LOGS_DIR even when later dispatcher invocations run with
-- a different LOGS_DIR environment value.

ALTER TABLE workflows ADD COLUMN logs_dir TEXT;
