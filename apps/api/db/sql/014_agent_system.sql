-- Marca los agentes de sistema (integrados en el runtime) y evita borrarlos.
ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_system boolean NOT NULL DEFAULT false;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'custom';
