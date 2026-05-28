-- Migration 008: Add clarification_history JSON column to workflows table.
-- Stores embedded Q&A rounds from WorkPlan clarification interactions.
ALTER TABLE workflows ADD COLUMN clarification_history TEXT;
