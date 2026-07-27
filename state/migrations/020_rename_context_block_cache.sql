-- Migration 020: Rename the synthesized-context block store.
--
-- Existing content-addressed rendered blocks remain durable and retain every
-- row during this terminology change.

ALTER TABLE context_block_cache RENAME TO synthesized_context_blocks;
