-- 031 — Hablantes (diarización) como nodos del knowledge graph.
-- Permite representar cada hablante como nodo 'Speaker' (etiquetado con
-- speakers.display_name) y enlazarlo al media donde interviene (HAS_SPEAKER) y a
-- la parte a la que quedó resuelto (IS_PARTY). Así, renombrar un hablante se
-- refleja al reconstruir el grafo.

ALTER TABLE graph_nodes DROP CONSTRAINT IF EXISTS graph_nodes_node_type_check;
ALTER TABLE graph_nodes ADD CONSTRAINT graph_nodes_node_type_check
  CHECK (node_type IN ('Case','Person','Organization','Document','Claim','Fact','Evidence','Event',
                       'LegalRule','Decision','Issue','Speaker'));

ALTER TABLE graph_edges DROP CONSTRAINT IF EXISTS graph_edges_edge_type_check;
ALTER TABLE graph_edges ADD CONSTRAINT graph_edges_edge_type_check
  CHECK (edge_type IN ('ASSERTS','SUPPORTS','CONTRADICTS','REFUTES','CITES','PARTICIPATED_IN',
                       'TESTIFIED_IN','DECIDES','APPLIES','DERIVED_FROM','MENTIONS','ABOUT',
                       'HAS_SPEAKER','IS_PARTY'));
