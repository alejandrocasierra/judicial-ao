-- 060 - Tipos de arista del grafo para hechos y posiciones de parte.
ALTER TABLE graph_edges DROP CONSTRAINT IF EXISTS graph_edges_edge_type_check;
ALTER TABLE graph_edges ADD CONSTRAINT graph_edges_edge_type_check
  CHECK (edge_type IN (
    'ASSERTS','SUPPORTS','CONTRADICTS','REFUTES','CITES','PARTICIPATED_IN',
    'TESTIFIED_IN','DECIDES','APPLIES','DERIVED_FROM','MENTIONS','ABOUT',
    'HAS_SPEAKER','IS_PARTY','PRECEDES','CAUSES','APPEALS','RESPONDS_TO',
    'REFERS_TO','ALLEGES','DENIES','ADMITS','POSITION_ON',
    'DECISION_PROVES_FACT','FACT_ABOUT_ISSUE'
  ));
