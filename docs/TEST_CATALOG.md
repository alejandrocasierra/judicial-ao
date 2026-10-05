# Catálogo de pruebas (generado)

> Archivo generado por `scripts/gen_test_catalog.py`. No editar a mano.

Total de funciones de prueba: **434** (los casos parametrizados multiplican las ejecuciones).

## Unitarias — 205 funciones

| Prueba | Archivo | Qué verifica | Parametrizada |
|---|---|---|---|
| `test_ut_abs_01_extract_terms_filters_stopwords` | test_abstention.py | UT-ABS — detector de abstención. |  |
| `test_ut_abs_02_should_abstain_with_no_coverage` | test_abstention.py | UT-ABS — detector de abstención. |  |
| `test_ut_abs_03_should_not_abstain_with_high_coverage` | test_abstention.py | UT-ABS — detector de abstención. |  |
| `test_ut_agn_01_tool_loop_then_synthesis` | test_agent.py | UT-AGN — agent loop y síntesis final. |  |
| `test_ut_agn_02_no_evidence` | test_agent.py | UT-AGN — agent loop y síntesis final. |  |
| `test_ut_agt_01_ilike_terms` | test_agent_tools.py | UT-AGT — agent tools allowlist. |  |
| `test_ut_agt_02_ilike_terms_short_words` | test_agent_tools.py | UT-AGT — agent tools allowlist. |  |
| `test_ut_agt_03_execute_dispatch` | test_agent_tools.py | UT-AGT — agent tools allowlist. |  |
| `test_ut_agt_04_unknown_tool` | test_agent_tools.py | UT-AGT — agent tools allowlist. |  |
| `test_ut_ans_01_invented_citation_is_unsupported` | test_answer_contract.py | UT-ANS — contrato de respuesta y aislamiento de contenido no confiable. |  |
| `test_ut_ans_02_claim_without_citation_is_unsupported` | test_answer_contract.py | UT-ANS — contrato de respuesta y aislamiento de contenido no confiable. |  |
| `test_ut_ans_03_non_json_output_is_rejected` | test_answer_contract.py | UT-ANS — contrato de respuesta y aislamiento de contenido no confiable. |  |
| `test_ut_ans_04_fenced_json_is_accepted` | test_answer_contract.py | UT-ANS — contrato de respuesta y aislamiento de contenido no confiable. |  |
| `test_ut_ans_05_untrusted_content_cannot_break_out` | test_answer_contract.py | UT-ANS — contrato de respuesta y aislamiento de contenido no confiable. |  |
| `test_ut_ans_06_system_prompt_is_versioned_and_localized` | test_answer_contract.py | UT-ANS — contrato de respuesta y aislamiento de contenido no confiable. |  |
| `test_ut_ans_10_grounding_rejects_invented_numbers` | test_answer_contract.py | UT-ANS — contrato de respuesta y aislamiento de contenido no confiable. |  |
| `test_ut_ans_11_grounding_rejects_unrelated_text_with_valid_handle` | test_answer_contract.py | UT-ANS — contrato de respuesta y aislamiento de contenido no confiable. |  |
| `test_ut_ans_12_grounding_is_accent_and_case_insensitive` | test_answer_contract.py | UT-ANS — contrato de respuesta y aislamiento de contenido no confiable. |  |
| `test_ut_ans_01_rrf_fuses_disjoint_lists` | test_answering.py | UT-ANS — recuperación híbrida FTS + vector + RRF. |  |
| `test_ut_ans_02_retrieve_uses_chunks` | test_answering.py | UT-ANS — recuperación híbrida FTS + vector + RRF. |  |
| `test_ut_asr_01_fake_asr_returns_segments` | test_asr.py | UT-ASR — proveedores ASR (Fase 3). |  |
| `test_ut_asr_02_get_provider_is_fake_in_tests` | test_asr.py | UT-ASR — proveedores ASR (Fase 3). |  |
| `test_ut_asr_03_whisper_provider_class_exists` | test_asr.py | UT-ASR — proveedores ASR (Fase 3). |  |
| `test_ut_asr_04_extract_audio_to_wav_rejects_empty_bytes` | test_asr.py | UT-ASR — proveedores ASR (Fase 3). |  |
| `test_ut_ct_01_mmss` | test_case_tools.py | UT-CT — capa de tools compartida del chat IA (case_tools). |  |
| `test_ut_ct_02_registry_contains_plan_tools` | test_case_tools.py | UT-CT — capa de tools compartida del chat IA (case_tools). |  |
| `test_ut_ct_03_merge_items_dedupes_and_rehandles` | test_case_tools.py | UT-CT — capa de tools compartida del chat IA (case_tools). |  |
| `test_ut_ct_04_file_cards_deduped` | test_case_tools.py | UT-CT — capa de tools compartida del chat IA (case_tools). |  |
| `test_ut_ct_05_load_agent_prompt_merges_skills` | test_case_tools.py | UT-CT — capa de tools compartida del chat IA (case_tools). |  |
| `test_ut_ct_06_agent_loop_declares_attachments` | test_case_tools.py | UT-CT — capa de tools compartida del chat IA (case_tools). |  |
| `test_ut_ct_07_invalid_ids_return_error_without_touching_db` | test_case_tools.py | UT-CT — capa de tools compartida del chat IA (case_tools). |  |
| `test_ut_ct_08_correct_ocr_requires_confirmation` | test_case_tools.py | UT-CT — capa de tools compartida del chat IA (case_tools). |  |
| `test_ut_ct_09_correct_ocr_confirm_propagates` | test_case_tools.py | UT-CT — capa de tools compartida del chat IA (case_tools). |  |
| `test_ut_ct_10_correct_ocr_rejects_identical_text` | test_case_tools.py | UT-CT — capa de tools compartida del chat IA (case_tools). |  |
| `test_ut_ct_11_write_tools_need_identity` | test_case_tools.py | UT-CT — capa de tools compartida del chat IA (case_tools). |  |
| `test_ut_ct_13_speaker_candidate` | test_case_tools.py | UT-CT — capa de tools compartida del chat IA (case_tools). |  |
| `test_ut_ct_12_suggest_reprocess_diagnostic` | test_case_tools.py | UT-CT — capa de tools compartida del chat IA (case_tools). |  |
| `test_ut_chk_01_document_indexa_ambos_modos_por_pagina` | test_chunking.py | UT-CHK — chunking jurídico. |  |
| `test_ut_chk_02_document_omite_paginas_vacias` | test_chunking.py | UT-CHK — chunking jurídico. |  |
| `test_ut_chk_03_media_groups_same_speaker` | test_chunking.py | UT-CHK — chunking jurídico. |  |
| `test_ut_chk_04_media_splits_on_large_gap` | test_chunking.py | UT-CHK — chunking jurídico. |  |
| `test_ut_chk_05_claims_skip_empty_text` | test_chunking.py | UT-CHK — chunking jurídico. |  |
| `test_ut_cfg_01_no_field_has_default` | test_config.py | UT-CFG — configuración sin valores quemados. |  |
| `test_ut_cfg_02_missing_variable_fails` | test_config.py | UT-CFG — configuración sin valores quemados. |  |
| `test_ut_cfg_03_production_forbids_fake_llm_and_basic_scanner` | test_config.py | UT-CFG — configuración sin valores quemados. |  |
| `test_ut_cfg_04_weak_jwt_secret_rejected` | test_config.py | UT-CFG — configuración sin valores quemados. |  |
| `test_ut_cfg_05_real_llm_requires_key` | test_config.py | UT-CFG — configuración sin valores quemados. |  |
| `test_ut_cfg_06_production_forbids_eager_celery` | test_config.py | En producción el modo eager ejecutaría los jobs dentro de la API: prohibido. |  |
| `test_ut_cfg_07_production_with_real_backends_boots` | test_config.py | Control: con backends reales y eager=false, la configuración de producción es válida. |  |
| `test_ut_cfg_08_stale_threshold_must_exceed_job_time_limit` | test_config.py | Si el sweeper barre antes de que un job legítimo pueda terminar, lo ejecutaría dos veces. |  |
| `test_ut_cfg_09_stale_threshold_at_double_time_limit_is_valid` | test_config.py | Control: el umbral a 2x del time limit es válido en cualquier entorno. |  |
| `test_ut_cor_01_confirmation` | test_correction_intent.py | UT-COR — detección de intención de corrección (Fase 6). | sí |
| `test_ut_cor_02_cancellation` | test_correction_intent.py | UT-COR — detección de intención de corrección (Fase 6). | sí |
| `test_ut_cor_03_not_confirmation_or_cancellation` | test_correction_intent.py | UT-COR — detección de intención de corrección (Fase 6). | sí |
| `test_ut_cor_04_correction_intent` | test_correction_intent.py | UT-COR — detección de intención de corrección (Fase 6). | sí |
| `test_ut_cor_05_reprocess_question` | test_correction_intent.py | UT-COR — detección de intención de corrección (Fase 6). | sí |
| `test_ut_cor_06_hint_prefers_correction_over_reprocess` | test_correction_intent.py | UT-COR — detección de intención de corrección (Fase 6). |  |
| `test_ut_cor_07_plain_question_has_no_hint` | test_correction_intent.py | UT-COR — detección de intención de corrección (Fase 6). |  |
| `test_ut_diar_01_energy_fallback_creates_segments` | test_diarization.py | UT-DIAR — diarización de hablantes (Fase 3). |  |
| `test_ut_diar_02_diarize_uses_pyannote_when_token_set` | test_diarization.py | UT-DIAR — diarización de hablantes (Fase 3). |  |
| `test_ut_st_01_case_transitions` | test_domain.py | UT-ST / UT-JUR — máquinas de estado y jurisdicción. | sí |
| `test_ut_st_02_archived_and_succeeded_are_terminal` | test_domain.py | UT-ST / UT-JUR — máquinas de estado y jurisdicción. |  |
| `test_ut_st_04_job_requeue_transitions` | test_domain.py | El sweeper sólo puede devolver a QUEUED jobs huérfanos en RUNNING/RETRYING. | sí |
| `test_ut_st_03_retry_policy` | test_domain.py | UT-ST / UT-JUR — máquinas de estado y jurisdicción. | sí |
| `test_ut_jur_01_colombian_case_number` | test_domain.py | UT-ST / UT-JUR — máquinas de estado y jurisdicción. | sí |
| `test_ut_jur_02_citation_normalization_is_canonical` | test_domain.py | UT-ST / UT-JUR — máquinas de estado y jurisdicción. |  |
| `test_ut_jur_03_unknown_jurisdiction_invalid` | test_domain.py | UT-ST / UT-JUR — máquinas de estado y jurisdicción. |  |
| `test_ut_emb_01_fake_returns_unit_vectors` | test_embeddings.py | UT-EMB — proveedores de embeddings. |  |
| `test_ut_emb_02_fake_is_deterministic` | test_embeddings.py | UT-EMB — proveedores de embeddings. |  |
| `test_ut_emb_03_fake_respects_max_chars` | test_embeddings.py | UT-EMB — proveedores de embeddings. |  |
| `test_ut_emb_04_factory_uses_config` | test_embeddings.py | UT-EMB — proveedores de embeddings. |  |
| `test_ut_emb_05_unknown_provider_raises` | test_embeddings.py | UT-EMB — proveedores de embeddings. |  |
| `test_ut_evl_01_citation_matches` | test_eval_runner.py | UT-EVL — golden eval runner. |  |
| `test_ut_evl_02_evaluate_retrieval_only_pass` | test_eval_runner.py | UT-EVL — golden eval runner. |  |
| `test_ut_evl_03_evaluate_abstain` | test_eval_runner.py | UT-EVL — golden eval runner. |  |
| `test_ut_evl_04_evaluate_full_checks` | test_eval_runner.py | UT-EVL — golden eval runner. |  |
| `test_ut_ev_01_source_label_transcript_has_minute_speaker_file` | test_evidence_hints.py | UT-EV — la evidencia que ve el modelo incluye minuto, hablante y archivo. |  |
| `test_ut_ev_02_source_label_document_has_page_and_file` | test_evidence_hints.py | UT-EV — la evidencia que ve el modelo incluye minuto, hablante y archivo. |  |
| `test_ut_ev_03_evidence_hint_summarizes_transcript` | test_evidence_hints.py | UT-EV — la evidencia que ve el modelo incluye minuto, hablante y archivo. |  |
| `test_ut_ev_04_evidence_hint_summarizes_document_page` | test_evidence_hints.py | UT-EV — la evidencia que ve el modelo incluye minuto, hablante y archivo. |  |
| `test_ut_ev_05_grounding_accepts_minute_and_speaker_from_labels` | test_evidence_hints.py | Una afirmación que cita «minuto 01:58 · hablante» debe validarse aunque ese |  |
| `test_ut_ev_06_grounding_ignores_reporting_words` | test_evidence_hints.py | Una lista de minutos con palabras de reporte («según las marcas de tiempo…») no se |  |
| `test_ut_ev_07_grounding_still_rejects_invented_number` | test_evidence_hints.py | UT-EV — la evidencia que ve el modelo incluye minuto, hablante y archivo. |  |
| `test_ut_file_01_sniff_real_types` | test_files.py | UT-FILE — validación de archivos. |  |
| `test_ut_file_02_sanitize_filename` | test_files.py | UT-FILE — validación de archivos. | sí |
| `test_ut_file_03_eicar_detected` | test_files.py | UT-FILE — validación de archivos. |  |
| `test_ut_file_04_plain_zip_is_not_docx` | test_files.py | UT-FILE — validación de archivos. |  |
| `test_ut_grp_01_table_for_target` | test_graph.py | UT-GRP — knowledge graph. |  |
| `test_ut_grp_02_build_inserts_nodes_and_edges` | test_graph.py | UT-GRP — knowledge graph. |  |
| `test_ut_grp_03_evidence_matrix` | test_graph.py | UT-GRP — knowledge graph. |  |
| `test_ut_grp_04_find_node` | test_graph.py | UT-GRP — knowledge graph. |  |
| `test_ut_ocrd_01_reference_lines_limpia_vacias` | test_handwriting_dataset.py | UT-OCRD — alineación de líneas para el corpus de caligrafía. |  |
| `test_ut_ocrd_02_align_monotono_con_ruido` | test_handwriting_dataset.py | UT-OCRD — alineación de líneas para el corpus de caligrafía. |  |
| `test_ut_ocrd_03_descarta_parejas_poco_similares` | test_handwriting_dataset.py | UT-OCRD — alineación de líneas para el corpus de caligrafía. |  |
| `test_ut_ocrd_04_build_pairs_devuelve_etiquetas_corregidas` | test_handwriting_dataset.py | UT-OCRD — alineación de líneas para el corpus de caligrafía. |  |
| `test_ut_ocrd_05_similarity_basica` | test_handwriting_dataset.py | UT-OCRD — alineación de líneas para el corpus de caligrafía. |  |
| `test_ut_hw_01_crop_box_recorta_y_limita_a_la_imagen` | test_handwriting_ocr.py | UT-OCR-HW — segunda pasada de reconocimiento de manuscritos (sin descargar modelos). |  |
| `test_ut_hw_02_refina_solo_lineas_de_baja_confianza` | test_handwriting_ocr.py | UT-OCR-HW — segunda pasada de reconocimiento de manuscritos (sin descargar modelos). |  |
| `test_ut_hw_03_sin_reconocedor_no_cambia_nada` | test_handwriting_ocr.py | UT-OCR-HW — segunda pasada de reconocimiento de manuscritos (sin descargar modelos). |  |
| `test_ut_hw_04_reconocedor_que_falla_no_rompe_la_pagina` | test_handwriting_ocr.py | UT-OCR-HW — segunda pasada de reconocimiento de manuscritos (sin descargar modelos). |  |
| `test_ut_i18n_01_catalogs_have_same_keys` | test_i18n.py | UT-I18N — compatibilidad español/inglés. |  |
| `test_ut_i18n_02_negotiation` | test_i18n.py | UT-I18N — compatibilidad español/inglés. |  |
| `test_ut_i18n_03_every_error_code_is_translated` | test_i18n.py | UT-I18N — compatibilidad español/inglés. |  |
| `test_ut_i18n_04_translations_differ` | test_i18n.py | UT-I18N — compatibilidad español/inglés. |  |
| `test_ut_i18n_05_enums_cover_db_values` | test_i18n.py | UT-I18N — compatibilidad español/inglés. |  |
| `test_ut_idx_01_parse_cuaderno_excludes_empty_and_closing` | test_index_xlsx.py | UT-IDX — parser de índices XLSX (Fase 1). |  |
| `test_ut_idx_02_extracts_numbers_and_lowercases_format` | test_index_xlsx.py | UT-IDX — parser de índices XLSX (Fase 1). |  |
| `test_ut_idx_03_keeps_duplicate_orders_as_separate_entries` | test_index_xlsx.py | UT-IDX — parser de índices XLSX (Fase 1). |  |
| `test_ut_idx_04_parse_general_lists_cuadernos` | test_index_xlsx.py | UT-IDX — parser de índices XLSX (Fase 1). |  |
| `test_ut_idx_01_index_document_deletes_and_inserts` | test_indexing.py | UT-IDX — indexación de chunks y embeddings. |  |
| `test_ut_idx_02_vector_literal_format` | test_indexing.py | UT-IDX — indexación de chunks y embeddings. |  |
| `test_ut_lex_06_llm_routing_uses_task` | test_legal_extraction.py | UT-LEX — extracción jurídica con LLM (Fase 4). |  |
| `test_ut_lex_01_extract_entities_persists_and_cites` | test_legal_extraction.py | UT-LEX — extracción jurídica con LLM (Fase 4). |  |
| `test_ut_lex_02_extract_claims_persists_and_cites` | test_legal_extraction.py | UT-LEX — extracción jurídica con LLM (Fase 4). |  |
| `test_ut_lex_03_invalid_schema_returns_error` | test_legal_extraction.py | UT-LEX — extracción jurídica con LLM (Fase 4). |  |
| `test_ut_lex_04_untrusted_content_is_escaped` | test_legal_extraction.py | UT-LEX — extracción jurídica con LLM (Fase 4). |  |
| `test_ut_lex_05_prompt_is_versioned_and_localized` | test_legal_extraction.py | UT-LEX — extracción jurídica con LLM (Fase 4). |  |
| `test_ut_llm_01_chat_completions_url` | test_llm_urls.py | UT-LLM — construcción de endpoints por proveedor (cualquier IA con cualquier API key). | sí |
| `test_ut_llm_02_messages_url` | test_llm_urls.py | UT-LLM — construcción de endpoints por proveedor (cualquier IA con cualquier API key). | sí |
| `test_ut_media_00_self_intro_names_for_speakers_without_video` | test_media_pipeline.py | 'quien les habla, X' identifica al hablante sin video (p. ej. el juez). |  |
| `test_ut_media_01_assign_speakers_matches_best_overlap_and_name` | test_media_pipeline.py | UT-MEDIA — pipeline de procesamiento de audio/video (Fase 3). |  |
| `test_ut_media_02_resolve_label_names_por_votacion` | test_media_pipeline.py | Cada SPEAKER_xx toma el nombre activo más frecuente durante sus turnos. |  |
| `test_ut_media_03_process_media_persists_segments_and_speakers` | test_media_pipeline.py | UT-MEDIA — pipeline de procesamiento de audio/video (Fase 3). |  |
| `test_ut_met_01_metrics_endpoint_has_content` | test_metrics.py | UT-MET — métricas y alertas. |  |
| `test_ut_met_02_track_stage_records_latency` | test_metrics.py | UT-MET — métricas y alertas. |  |
| `test_ut_met_03_track_llm_usage` | test_metrics.py | UT-MET — métricas y alertas. |  |
| `test_ut_met_04_track_job_counters` | test_metrics.py | UT-MET — métricas y alertas. |  |
| `test_ut_met_05_quality_gauges` | test_metrics.py | UT-MET — métricas y alertas. |  |
| `test_ut_met_06_alerts_log_does_not_raise` | test_metrics.py | UT-MET — métricas y alertas. |  |
| `test_ut_mig_01_logical_key_is_posix_relative` | test_migrate_storage.py | UT-MIG — clave lógica de la migración de storage local -> bucket. |  |
| `test_ut_mig_02_handles_files_and_media` | test_migrate_storage.py | UT-MIG — clave lógica de la migración de storage local -> bucket. |  |
| `test_ut_ocr_01_fake_ocr_returns_pages` | test_ocr.py | UT-OCR — proveedores OCR (Fase 2). |  |
| `test_ut_ocr_02_get_provider_is_fake_in_tests` | test_ocr.py | UT-OCR — proveedores OCR (Fase 2). |  |
| `test_ut_ocr_03_tesseract_provider_class_exists` | test_ocr.py | UT-OCR — proveedores OCR (Fase 2). |  |
| `test_ut_ocr_04_layout_text_respeta_orden_espacial` | test_ocr.py | UT-OCR — proveedores OCR (Fase 2). |  |
| `test_ut_ocr_05_layout_text_separa_parrafos` | test_ocr.py | UT-OCR — proveedores OCR (Fase 2). |  |
| `test_ut_ocr_06_layout_text_cajas_rapidocr` | test_ocr.py | UT-OCR — proveedores OCR (Fase 2). |  |
| `test_ut_ocr_07_clean_conserva_saltos_y_colapsa_exceso` | test_ocr.py | UT-OCR — proveedores OCR (Fase 2). |  |
| `test_ut_ocr_08_docling_latin_es_proveedor_de_reconocimiento_latino` | test_ocr.py | El proveedor docling_latin usa el modelo latino sin cambiar el resto del pipeline. |  |
| `test_ut_ocr_09_docling_layout_es_proveedor_con_layout_real` | test_ocr.py | El proveedor docling_layout usa el DocumentConverter de Docling para análisis de estructura. |  |
| `test_ut_ocr_10_layout_text_detecta_etiqueta_valor` | test_ocr.py | En formularios, el hueco horizontal grande entre etiqueta y valor se conserva. |  |
| `test_ut_ocr_11_layout_text_separa_secciones` | test_ocr.py | Encabezados de sección en mayúsculas se reconocen como líneas propias. |  |
| `test_ut_ocr_12_document_ai_parse_preserva_orden_con_offset` | test_ocr.py | El chunking de Document AI numera las páginas globalmente (offset por chunk). |  |
| `test_ut_ocr_13_document_ai_parse_sin_page_number_usa_indice` | test_ocr.py | Si Document AI omite pageNumber, el parser usa el índice + offset. |  |
| `test_ut_ocr_15_document_ai_tandas_154_paginas` | test_ocr.py | 154 páginas → 11 tandas de 15 (10 completas + 4 finales), sin huecos ni solapes. |  |
| `test_ut_ocr_16_document_ai_tandas_casos_limite` | test_ocr.py | Casos límite: 0, 1, 15 y 16 páginas. |  |
| `test_ut_ocr_17_document_ai_usa_orden_nativo_no_relayout` | test_ocr.py | El texto de Document AI se toma en su orden nativo (sin re-layout geométrico). |  |
| `test_ut_ocr_18_page_native_text_concatena_segmentos` | test_ocr.py | `_page_native_text` une todos los segmentos del textAnchor de la página. |  |
| `test_ut_ocr_14_document_ai_parse_bounding_boxes_normalizados` | test_ocr.py | Los bounding boxes normalizados (0-1) se convierten a píxeles de página. |  |
| `test_ut_ocrm_01_cer_sustitucion_y_longitud` | test_ocr_metrics.py | UT-OCRM — métricas de OCR (CER/WER). |  |
| `test_ut_ocrm_02_cer_referencia_vacia` | test_ocr_metrics.py | UT-OCRM — métricas de OCR (CER/WER). |  |
| `test_ut_ocrm_03_normaliza_acentos_y_caja` | test_ocr_metrics.py | UT-OCRM — métricas de OCR (CER/WER). |  |
| `test_ut_ocrm_04_wer_palabras` | test_ocr_metrics.py | UT-OCRM — métricas de OCR (CER/WER). |  |
| `test_ut_ocrm_05_edit_distance` | test_ocr_metrics.py | UT-OCRM — métricas de OCR (CER/WER). |  |
| `test_ut_ocrp_01_quita_guiones_bajos_pegados_a_etiquetas` | test_ocr_postprocess.py | UT-OCRP — post-proceso del texto OCR (limpieza de formularios). |  |
| `test_ut_ocrp_02_elimina_lineas_de_regla_del_formulario` | test_ocr_postprocess.py | UT-OCRP — post-proceso del texto OCR (limpieza de formularios). |  |
| `test_ut_ocrp_03_conserva_el_texto_util_entre_reglas` | test_ocr_postprocess.py | UT-OCRP — post-proceso del texto OCR (limpieza de formularios). |  |
| `test_ut_ocrp_04_elimina_glifos_basura` | test_ocr_postprocess.py | UT-OCRP — post-proceso del texto OCR (limpieza de formularios). |  |
| `test_ut_ocrp_05_normaliza_espacios_y_lineas_en_blanco` | test_ocr_postprocess.py | UT-OCRP — post-proceso del texto OCR (limpieza de formularios). |  |
| `test_ut_ocrp_06_texto_vacio_o_nulo` | test_ocr_postprocess.py | UT-OCRP — post-proceso del texto OCR (limpieza de formularios). |  |
| `test_ut_ocrp_08_marcas_de_casilla_se_vuelven_texto` | test_ocr_postprocess.py | UT-OCRP — post-proceso del texto OCR (limpieza de formularios). |  |
| `test_ut_ocrp_09_conserva_alineacion_interna_y_quita_espacios_finales` | test_ocr_postprocess.py | UT-OCRP — post-proceso del texto OCR (limpieza de formularios). |  |
| `test_ut_ocrp_07_clean_form_artifacts_no_toca_contenido_real` | test_ocr_postprocess.py | UT-OCRP — post-proceso del texto OCR (limpieza de formularios). |  |
| `test_ut_hint_01_always_general_all_sources` | test_query_hints.py | UT-HINT — recordatorio general de búsqueda (todas las fuentes), sin reglas por palabra. | sí |
| `test_ut_hint_02_merge_drops_empty` | test_query_hints.py | UT-HINT — recordatorio general de búsqueda (todas las fuentes), sin reglas por palabra. |  |
| `test_ut_hint_03_locate_intent` | test_query_hints.py | UT-HINT — recordatorio general de búsqueda (todas las fuentes), sin reglas por palabra. | sí |
| `test_ut_hint_04_not_locate` | test_query_hints.py | UT-HINT — recordatorio general de búsqueda (todas las fuentes), sin reglas por palabra. | sí |
| `test_ut_rng_01_closed_range` | test_range.py | UT-RNG — parseo de HTTP Range para el streaming de media. |  |
| `test_ut_rng_02_open_ended_range` | test_range.py | UT-RNG — parseo de HTTP Range para el streaming de media. |  |
| `test_ut_rng_03_suffix_range` | test_range.py | UT-RNG — parseo de HTTP Range para el streaming de media. |  |
| `test_ut_rng_04_clamps_end_to_total` | test_range.py | UT-RNG — parseo de HTTP Range para el streaming de media. |  |
| `test_ut_rng_05_invalid_is_unsatisfiable` | test_range.py | UT-RNG — parseo de HTTP Range para el streaming de media. |  |
| `test_ut_rl_01_org_check_allows_under_limit` | test_ratelimit_org.py | UT-RL — rate limiting por organización. |  |
| `test_ut_rl_02_org_check_blocks_over_limit` | test_ratelimit_org.py | UT-RL — rate limiting por organización. |  |
| `test_ut_rl_03_unknown_bucket_is_noop` | test_ratelimit_org.py | UT-RL — rate limiting por organización. |  |
| `test_ut_rbac_01_intersection_analyst_as_reviewer_cannot_review` | test_rbac.py | UT-RBAC — permisos efectivos = rol org ∩ rol expediente. |  |
| `test_ut_rbac_02_admin_has_everything_without_membership` | test_rbac.py | UT-RBAC — permisos efectivos = rol org ∩ rol expediente. |  |
| `test_ut_rbac_03_no_membership_no_access` | test_rbac.py | UT-RBAC — permisos efectivos = rol org ∩ rol expediente. |  |
| `test_ut_rbac_04_read_only_cannot_write` | test_rbac.py | UT-RBAC — permisos efectivos = rol org ∩ rol expediente. |  |
| `test_ut_rbac_05_only_admin_has_org_level_sensitive_perms` | test_rbac.py | UT-RBAC — permisos efectivos = rol org ∩ rol expediente. |  |
| `test_ut_sto_01_normalize_prefix` | test_storage_prefix.py | UT-STO — carpeta (prefijo) del bucket para Google Cloud Storage / S3. | sí |
| `test_ut_sto_02_prefixed` | test_storage_prefix.py | UT-STO — carpeta (prefijo) del bucket para Google Cloud Storage / S3. | sí |
| `test_ut_teams_01_looks_like_name_filters_noise` | test_teams_visual_id.py | UT-TEAMS — identificación visual del HABLANTE ACTIVO en Teams (Fase 3). |  |
| `test_ut_teams_02_is_highlighted_detects_blue_background` | test_teams_visual_id.py | UT-TEAMS — identificación visual del HABLANTE ACTIVO en Teams (Fase 3). |  |
| `test_ut_teams_03_detect_active_speaker_picks_highlighted_name` | test_teams_visual_id.py | UT-TEAMS — identificación visual del HABLANTE ACTIVO en Teams (Fase 3). |  |
| `test_ut_teams_04_timeline_returns_active_names` | test_teams_visual_id.py | UT-TEAMS — identificación visual del HABLANTE ACTIVO en Teams (Fase 3). |  |
| `test_ut_vrf_01_supported_claim` | test_verification.py | UT-VRF — verificación semántica anti-alucinación. |  |
| `test_ut_vrf_02_bad_json_returns_empty` | test_verification.py | UT-VRF — verificación semántica anti-alucinación. |  |
| `test_ut_wrk_01_every_schema_job_type_has_handler` | test_workers.py | UT-WRK — workers: registro de handlers, transiciones del executor e idempotencia. |  |
| `test_ut_wrk_02_stub_result_is_structured` | test_workers.py | UT-WRK — workers: registro de handlers, transiciones del executor e idempotencia. |  |
| `test_ut_wrk_03_decide_action` | test_workers.py | UT-WRK — workers: registro de handlers, transiciones del executor e idempotencia. |  |
| `test_ut_wrk_04_happy_path_marks_succeeded_and_records` | test_workers.py | UT-WRK — workers: registro de handlers, transiciones del executor e idempotencia. |  |
| `test_ut_wrk_05_terminal_or_claimed_job_is_not_rerun` | test_workers.py | UT-WRK — workers: registro de handlers, transiciones del executor e idempotencia. | sí |
| `test_ut_wrk_06_invisible_or_missing_job_is_skipped` | test_workers.py | UT-WRK — workers: registro de handlers, transiciones del executor e idempotencia. |  |
| `test_ut_wrk_07_unknown_job_type_fails_without_retry` | test_workers.py | UT-WRK — workers: registro de handlers, transiciones del executor e idempotencia. |  |
| `test_ut_wrk_08_retryable_error_marks_retrying_and_reraises` | test_workers.py | UT-WRK — workers: registro de handlers, transiciones del executor e idempotencia. |  |
| `test_ut_wrk_09_manual_review_error_fails_without_retry` | test_workers.py | UT-WRK — workers: registro de handlers, transiciones del executor e idempotencia. |  |
| `test_ut_wrk_10_unclassified_error_fails_as_internal` | test_workers.py | UT-WRK — workers: registro de handlers, transiciones del executor e idempotencia. |  |
| `test_ut_wrk_11_failed_job_is_claimed_via_retrying` | test_workers.py | FAILED -> RUNNING no existe en la máquina de estados: el claim pasa por RETRYING. |  |
| `test_ut_wrk_12_sweeper_requeues_stale_jobs_and_audits_with_actor` | test_workers.py | UT-WRK — workers: registro de handlers, transiciones del executor e idempotencia. |  |
| `test_ut_wrk_13_sweeper_is_idempotent_when_nothing_stale` | test_workers.py | UT-WRK — workers: registro de handlers, transiciones del executor e idempotencia. |  |
| `test_ut_wrk_14_sweeper_without_known_actor_only_logs` | test_workers.py | Jobs antiguos sin created_by: se recuperan pero no se auditan con actor NULL |  |
| `test_ut_wrk_15_retry_countdown_is_exponential_with_jitter` | test_workers.py | UT-WRK — workers: registro de handlers, transiciones del executor e idempotencia. |  |
| `test_ut_wrk_16_non_uuid_arguments_fail_without_touching_db` | test_workers.py | UT-WRK — workers: registro de handlers, transiciones del executor e idempotencia. |  |
| `test_ut_wrk_17_dispatcher_survives_broker_outage` | test_workers.py | Broker caído: enqueue_job no lanza; el job queda QUEUED y puede re-encolarse. |  |

## Integración (API + PostgreSQL) — 96 funciones

| Prueba | Archivo | Qué verifica | Parametrizada |
|---|---|---|---|
| `test_it_adm_01_list_users_requires_auth` | test_admin.py | IT-ADM — endpoints del panel de administración. |  |
| `test_it_adm_02_list_users_requires_permission` | test_admin.py | IT-ADM — endpoints del panel de administración. |  |
| `test_it_adm_03_admin_can_list_users` | test_admin.py | IT-ADM — endpoints del panel de administración. |  |
| `test_it_adm_04_create_user_sends_invite` | test_admin.py | IT-ADM — endpoints del panel de administración. |  |
| `test_it_adm_05_create_user_rejects_duplicate` | test_admin.py | IT-ADM — endpoints del panel de administración. |  |
| `test_it_adm_06_get_smtp_settings` | test_admin.py | IT-ADM — endpoints del panel de administración. |  |
| `test_it_adm_06b_smtp_roundtrip_persists` | test_admin.py | IT-ADM — endpoints del panel de administración. |  |
| `test_it_adm_07_trigger_backup` | test_admin.py | IT-ADM — endpoints del panel de administración. |  |
| `test_it_adm_08_list_agents_includes_system_agent` | test_admin.py | IT-ADM — endpoints del panel de administración. |  |
| `test_it_adm_09_agents_readable_by_query_users` | test_admin.py | El chat necesita listar agentes para "/": cualquier usuario con ai.query puede verlos. |  |
| `test_it_adm_10_model_custom_base_url_is_used_by_the_chat` | test_admin.py | La URL base por modelo permite apuntar a un proxy/VPS: el chat la usa tal cual. |  |
| `test_it_acr_query_with_selected_agent` | test_admin_crud.py | IT-ACR — CRUD de agentes, skills, modelos, roles y edición OCR/transcripción. |  |
| `test_it_acr_00_system_agent_and_role_are_editable` | test_admin_crud.py | IT-ACR — CRUD de agentes, skills, modelos, roles y edición OCR/transcripción. |  |
| `test_it_acr_01_agent_crud` | test_admin_crud.py | IT-ACR — CRUD de agentes, skills, modelos, roles y edición OCR/transcripción. |  |
| `test_it_acr_02_model_catalog_and_crud` | test_admin_crud.py | IT-ACR — CRUD de agentes, skills, modelos, roles y edición OCR/transcripción. |  |
| `test_it_acr_03_roles_list_and_custom` | test_admin_crud.py | IT-ACR — CRUD de agentes, skills, modelos, roles y edición OCR/transcripción. |  |
| `test_it_acr_04_roles_permissions_validation` | test_admin_crud.py | IT-ACR — CRUD de agentes, skills, modelos, roles y edición OCR/transcripción. |  |
| `test_it_acr_05_document_page_edit` | test_admin_crud.py | Corrige el OCR de una página semilla y restaura el estado exacto (no toca el corpus). |  |
| `test_it_acr_06_media_segments_edit` | test_admin_crud.py | IT-ACR — CRUD de agentes, skills, modelos, roles y edición OCR/transcripción. |  |
| `test_it_health_01_liveness_and_readiness` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_auth_01_login_and_me` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_auth_02_refresh_rotation_and_reuse_detection` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_case_01_create_valid_case` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_case_02_validation_errors` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. | sí |
| `test_it_case_03_list_shows_only_member_cases` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_case_04_optimistic_locking_and_transitions` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_doc_01_upload_dedup_download_integrity` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_doc_02_page_viewer` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_media_01_upload_audio` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_views_01_timeline_claims_facts_evidence` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_cit_01_all_seeded_citations_are_valid` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_cit_02_tampered_quote_hash_is_detected` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_query_01_grounded_answer_with_resolvable_citations` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_query_02_localized_insufficient_evidence` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_query_03_agent_strategy_returns_response` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_review_01_edit_keeps_original_ai_output` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_i18n_01_error_messages_follow_accept_language` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_proc_01_process_is_idempotent` | test_api_core.py | IT — flujos funcionales principales contra API + PostgreSQL real. |  |
| `test_it_bc_01_chat_skills_seeded_in_panel` | test_builtin_chat.py | IT-BC — skills y agentes sembrados del chat (Fase 5): idempotencia, enlace de |  |
| `test_it_bc_02_chat_agents_seeded_with_links` | test_builtin_chat.py | IT-BC — skills y agentes sembrados del chat (Fase 5): idempotencia, enlace de |  |
| `test_it_bc_03_seeding_is_idempotent` | test_builtin_chat.py | IT-BC — skills y agentes sembrados del chat (Fase 5): idempotencia, enlace de |  |
| `test_it_bc_04_user_facing_agents_seed_without_admin_visit` | test_builtin_chat.py | El chat (que usa /v1/agents) obtiene los agentes listos sin abrir el panel. |  |
| `test_it_bc_05_grill_agent_merges_skill_prompts_at_query` | test_builtin_chat.py | Elegir 'Grill-me jurídico' fusiona sus skills (interrogatorio + encapsulamiento) al agente. |  |
| `test_it_ct_01_query_with_attachment_returns_file_card` | test_chat_tools.py | IT-CT — chat IA: adjuntos "@" reales, tools de lectura y correcciones OCR/ASR |  |
| `test_it_ct_02_unknown_attachment_is_ignored` | test_chat_tools.py | IT-CT — chat IA: adjuntos "@" reales, tools de lectura y correcciones OCR/ASR |  |
| `test_it_ct_03_search_transcript_by_time_range` | test_chat_tools.py | IT-CT — chat IA: adjuntos "@" reales, tools de lectura y correcciones OCR/ASR |  |
| `test_it_ct_04_correct_ocr_page_via_tool_propagates` | test_chat_tools.py | IT-CT — chat IA: adjuntos "@" reales, tools de lectura y correcciones OCR/ASR |  |
| `test_it_ct_05_correct_transcript_segment_via_tool` | test_chat_tools.py | IT-CT — chat IA: adjuntos "@" reales, tools de lectura y correcciones OCR/ASR |  |
| `test_it_ct_06_suggest_reprocess_diagnoses_without_mutating` | test_chat_tools.py | IT-CT — chat IA: adjuntos "@" reales, tools de lectura y correcciones OCR/ASR |  |
| `test_it_ct_07_agent_prompt_includes_skill_prompts` | test_chat_tools.py | IT-CT — chat IA: adjuntos "@" reales, tools de lectura y correcciones OCR/ASR |  |
| `test_it_ct_08_speaker_name_search_returns_their_segments` | test_chat_tools.py | «¿en qué minuto habló Paola?» debe encontrar los segmentos del HABLANTE, no solo menciones. |  |
| `test_it_ct_09_find_person_returns_citable_sources` | test_chat_tools.py | «¿quién es X?» no se rinde si no hay ficha en el grafo: cita documentos/transcripciones. |  |
| `test_it_chat_01_session_crud` | test_chats.py | IT-CHAT — backend multi-turn: sesiones, historial al agente, adjuntos "@" |  |
| `test_it_chat_02_send_message_persists_with_attachments_and_file_cards` | test_chats.py | IT-CHAT — backend multi-turn: sesiones, historial al agente, adjuntos "@" |  |
| `test_it_chat_03_followup_receives_conversation_history` | test_chats.py | El follow-up ('¿y quién más estaba?') llega al agente CON los turnos previos. |  |
| `test_it_chat_04_messages_pagination` | test_chats.py | IT-CHAT — backend multi-turn: sesiones, historial al agente, adjuntos "@" |  |
| `test_it_chat_05_archive_permissions` | test_chats.py | IT-CHAT — backend multi-turn: sesiones, historial al agente, adjuntos "@" |  |
| `test_it_chat_06_unknown_session_is_404` | test_chats.py | IT-CHAT — backend multi-turn: sesiones, historial al agente, adjuntos "@" |  |
| `test_it_chat_07_locate_lists_all_locations` | test_chats.py | «¿Dónde se habla de X?» lista cada archivo+página/minuto, con citas (determinista). |  |
| `test_it_cor_01_full_flow_natural_language_then_confirmation` | test_correction_flow.py | IT-COR — corrección conversacional completa (Fase 6). |  |
| `test_it_cor_02_cancellation_leaves_case_untouched` | test_correction_flow.py | IT-COR — corrección conversacional completa (Fase 6). |  |
| `test_it_cor_03_reprocess_question_proposes_then_applies` | test_correction_flow.py | «¿Cómo mejoramos este OCR?» → diagnóstico (pendiente) → confirmar → reproceso encolado. |  |
| `test_it_cor_04_confirmation_without_pending_falls_through` | test_correction_flow.py | Un 'sí' sin corrección pendiente no rompe: sigue el flujo normal del agente. |  |
| `test_it_ocr_01_document_ocr_creates_pages_and_images` | test_document_ocr.py | IT-OCR — pipeline documental OCR (Fase 2). |  |
| `test_it_ocr_02_classification_indexes_document` | test_document_ocr.py | IT-OCR — pipeline documental OCR (Fase 2). |  |
| `test_it_ocr_03_reprocess_preserves_human_correction` | test_document_ocr.py | Reprocesar no debe pisar una página corregida a mano (human_corrected). |  |
| `test_it_exp_01_export_requires_auth` | test_export.py | IT-EXP — exportación CKP (Case Knowledge Package). |  |
| `test_it_exp_02_export_requires_permission` | test_export.py | IT-EXP — exportación CKP (Case Knowledge Package). |  |
| `test_it_exp_03_export_returns_zip` | test_export.py | IT-EXP — exportación CKP (Case Knowledge Package). |  |
| `test_it_exp_04_export_content_is_valid_jsonl` | test_export.py | IT-EXP — exportación CKP (Case Knowledge Package). |  |
| `test_it_imp_01_imports_full_expediente` | test_import_expediente.py | IT-IMP — importador masivo de expediente (Fase 1). |  |
| `test_it_imp_02_dry_run_does_not_write` | test_import_expediente.py | IT-IMP — importador masivo de expediente (Fase 1). |  |
| `test_it_imp_03_deduplicates_by_sha256` | test_import_expediente.py | IT-IMP — importador masivo de expediente (Fase 1). |  |
| `test_it_mcp_01_tools_list_exposes_case_tools` | test_mcp_server.py | IT-MCP — servidor MCP real (streamable HTTP + JWT + RLS + allowlist por rol). |  |
| `test_it_mcp_02_list_case_files_and_get_file` | test_mcp_server.py | IT-MCP — servidor MCP real (streamable HTTP + JWT + RLS + allowlist por rol). |  |
| `test_it_mcp_03_read_document_returns_pages` | test_mcp_server.py | IT-MCP — servidor MCP real (streamable HTTP + JWT + RLS + allowlist por rol). |  |
| `test_it_mcp_04_resources_files_and_graph_stats` | test_mcp_server.py | IT-MCP — servidor MCP real (streamable HTTP + JWT + RLS + allowlist por rol). |  |
| `test_it_mcp_05_no_token_is_401` | test_mcp_server.py | IT-MCP — servidor MCP real (streamable HTTP + JWT + RLS + allowlist por rol). |  |
| `test_it_mcp_06_cross_tenant_case_is_not_found` | test_mcp_server.py | IT-MCP — servidor MCP real (streamable HTTP + JWT + RLS + allowlist por rol). |  |
| `test_it_mcp_07_reviewer_role_cannot_correct_but_can_read` | test_mcp_server.py | Allowlist por rol: REVIEWER tiene ai.query (lee) pero no document.upload (no corrige). |  |
| `test_it_mcp_08_correction_requires_confirmation_preview` | test_mcp_server.py | Un usuario con permiso de escritura recibe VISTA PREVIA (no muta) sin confirm. |  |
| `test_it_obs_01_metrics_endpoint_exists` | test_observability.py | IT-OBS — observabilidad: métricas, alertas, backups. |  |
| `test_it_obs_02_metrics_include_required_metrics` | test_observability.py | IT-OBS — observabilidad: métricas, alertas, backups. |  |
| `test_it_obs_03_alerts_endpoint_requires_auth` | test_observability.py | IT-OBS — observabilidad: métricas, alertas, backups. |  |
| `test_it_obs_04_alerts_endpoint_returns_alerts` | test_observability.py | IT-OBS — observabilidad: métricas, alertas, backups. |  |
| `test_it_obs_05_backups_verify_rpo_requires_auth` | test_observability.py | IT-OBS — observabilidad: métricas, alertas, backups. |  |
| `test_it_obs_06_backups_verify_rpo_returns_status` | test_observability.py | IT-OBS — observabilidad: métricas, alertas, backups. |  |
| `test_it_obs_07_backups_restore_test_requires_auth` | test_observability.py | IT-OBS — observabilidad: métricas, alertas, backups. |  |
| `test_it_obs_08_backups_restore_test_returns_status` | test_observability.py | IT-OBS — observabilidad: métricas, alertas, backups. |  |
| `test_it_wrk_01_process_executes_jobs_to_succeeded` | test_workers_pipeline.py | IT-WRK — pipeline de jobs end-to-end: POST /process encola y el executor |  |
| `test_it_wrk_02_process_is_idempotent_and_does_not_rerun` | test_workers_pipeline.py | IT-WRK — pipeline de jobs end-to-end: POST /process encola y el executor |  |
| `test_it_wrk_03_executor_rerun_of_succeeded_job_is_noop` | test_workers_pipeline.py | IT-WRK — pipeline de jobs end-to-end: POST /process encola y el executor |  |
| `test_it_wrk_04_unknown_job_type_fails_deterministically` | test_workers_pipeline.py | IT-WRK — pipeline de jobs end-to-end: POST /process encola y el executor |  |
| `test_it_wrk_05_sweeper_requeues_only_stale_jobs` | test_workers_pipeline.py | Sweeper: un job viejo en RUNNING vuelve a QUEUED, se audita con su actor y se |  |
| `test_it_wrk_06_concurrent_process_requests_do_not_collide` | test_workers_pipeline.py | Race de idempotency_key: N requests concurrentes producen UN solo job y ningún 500 |  |
| `test_it_wrk_07_executor_cannot_touch_other_orgs_jobs` | test_workers_pipeline.py | RLS en el worker: un job de la org alfa es invisible para el org beta. |  |

## Seguridad — 98 funciones

| Prueba | Archivo | Qué verifica | Parametrizada |
|---|---|---|---|
| `test_sec_aud_01_hash_chain_is_linked` | test_audit_chain.py | SEC-AUD: la auditoría es completa, encadenada por hash y verificable (SSD §20.4, §96, amenaza T8). |  |
| `test_sec_aud_02_hash_is_recomputable` | test_audit_chain.py | SEC-AUD: la auditoría es completa, encadenada por hash y verificable (SSD §20.4, §96, amenaza T8). |  |
| `test_sec_aud_03_sensitive_actions_are_audited_with_request_id` | test_audit_chain.py | SEC-AUD: la auditoría es completa, encadenada por hash y verificable (SSD §20.4, §96, amenaza T8). |  |
| `test_sec_aud_04_audit_rows_carry_actor_and_org` | test_audit_chain.py | SEC-AUD: la auditoría es completa, encadenada por hash y verificable (SSD §20.4, §96, amenaza T8). |  |
| `test_sec_auth_01_no_user_enumeration` | test_auth_hardening.py | Usuario inexistente, contraseña errónea y usuario inactivo => misma respuesta. |  |
| `test_sec_auth_02_lockout_after_max_failed_attempts` | test_auth_hardening.py | SEC-AUTH: fuerza bruta, bloqueo, enumeración de usuarios y almacenamiento de contraseñas (amenaza T3). |  |
| `test_sec_auth_03_successful_login_resets_counter` | test_auth_hardening.py | SEC-AUTH: fuerza bruta, bloqueo, enumeración de usuarios y almacenamiento de contraseñas (amenaza T3). |  |
| `test_sec_auth_04_login_rate_limit` | test_auth_hardening.py | SEC-AUTH: fuerza bruta, bloqueo, enumeración de usuarios y almacenamiento de contraseñas (amenaza T3). |  |
| `test_sec_auth_05_passwords_are_argon2id_hashed_and_unique_salted` | test_auth_hardening.py | SEC-AUTH: fuerza bruta, bloqueo, enumeración de usuarios y almacenamiento de contraseñas (amenaza T3). |  |
| `test_sec_auth_06_me_never_exposes_secrets` | test_auth_hardening.py | SEC-AUTH: fuerza bruta, bloqueo, enumeración de usuarios y almacenamiento de contraseñas (amenaza T3). |  |
| `test_sec_auth_07_email_is_case_insensitive_but_strictly_validated` | test_auth_hardening.py | SEC-AUTH: fuerza bruta, bloqueo, enumeración de usuarios y almacenamiento de contraseñas (amenaza T3). |  |
| `test_sec_auth_08_failed_logins_are_audited_without_password` | test_auth_hardening.py | SEC-AUTH: fuerza bruta, bloqueo, enumeración de usuarios y almacenamiento de contraseñas (amenaza T3). |  |
| `test_sec_authz_01_forbidden_actions` | test_authz_matrix.py | SEC-AUTHZ: matriz de autorización (SSD §19.1, §20.1, amenaza T2 — escalamiento de privilegios). | sí |
| `test_sec_authz_02_non_members_get_404_not_403` | test_authz_matrix.py | No se revela la existencia del expediente a quien no es miembro (ni a otro tenant). | sí |
| `test_sec_authz_03_allowed_actions` | test_authz_matrix.py | SEC-AUTHZ: matriz de autorización (SSD §19.1, §20.1, amenaza T2 — escalamiento de privilegios). | sí |
| `test_sec_authz_04_anonymous_is_401` | test_authz_matrix.py | SEC-AUTHZ: matriz de autorización (SSD §19.1, §20.1, amenaza T2 — escalamiento de privilegios). | sí |
| `test_sec_authz_05_permissions_are_rechecked_after_role_change` | test_authz_matrix.py | Revocar la membresía surte efecto inmediato aunque el token siga vigente (no se cachean permisos en el JWT). |  |
| `test_sec_authz_06_deactivated_user_token_stops_working` | test_authz_matrix.py | SEC-AUTHZ: matriz de autorización (SSD §19.1, §20.1, amenaza T2 — escalamiento de privilegios). |  |
| `test_sec_err_01_unhandled_exception_is_generic_500` | test_errors.py | SEC-ERR: manejo de errores y cabeceras (OWASP A05). Nunca stack traces ni detalles internos. |  |
| `test_sec_err_02_security_headers` | test_errors.py | SEC-ERR: manejo de errores y cabeceras (OWASP A05). Nunca stack traces ni detalles internos. |  |
| `test_sec_err_03_request_id_echo_and_sanitization` | test_errors.py | SEC-ERR: manejo de errores y cabeceras (OWASP A05). Nunca stack traces ni detalles internos. |  |
| `test_sec_err_04_unknown_routes_use_error_contract` | test_errors.py | SEC-ERR: manejo de errores y cabeceras (OWASP A05). Nunca stack traces ni detalles internos. | sí |
| `test_sec_err_05_no_server_banner_leak` | test_errors.py | SEC-ERR: manejo de errores y cabeceras (OWASP A05). Nunca stack traces ni detalles internos. |  |
| `test_sec_imm_01_document_hash_and_uri_cannot_change` | test_immutability.py | SEC-IMM: integridad de la evidencia, legal hold y registros append-only (SSD §3.2, §20.4, amenazas T7/T8). |  |
| `test_sec_imm_02_documents_cannot_be_deleted` | test_immutability.py | SEC-IMM: integridad de la evidencia, legal hold y registros append-only (SSD §3.2, §20.4, amenazas T7/T8). |  |
| `test_sec_imm_03_processing_status_can_still_change` | test_immutability.py | Control positivo: la inmutabilidad es del ORIGINAL, no de los metadatos de procesamiento. |  |
| `test_sec_imm_04_cases_cannot_be_deleted` | test_immutability.py | SEC-IMM: integridad de la evidencia, legal hold y registros append-only (SSD §3.2, §20.4, amenazas T7/T8). |  |
| `test_sec_imm_05_audit_log_is_append_only` | test_immutability.py | SEC-IMM: integridad de la evidencia, legal hold y registros append-only (SSD §3.2, §20.4, amenazas T7/T8). |  |
| `test_sec_imm_06_reviews_are_append_only` | test_immutability.py | SEC-IMM: integridad de la evidencia, legal hold y registros append-only (SSD §3.2, §20.4, amenazas T7/T8). |  |
| `test_sec_imm_07_api_delete_document_is_405` | test_immutability.py | SEC-IMM: integridad de la evidencia, legal hold y registros append-only (SSD §3.2, §20.4, amenazas T7/T8). |  |
| `test_sec_imm_08_legal_hold_blocks_deletion_even_with_purge_flag` | test_immutability.py | SEC-IMM: integridad de la evidencia, legal hold y registros append-only (SSD §3.2, §20.4, amenazas T7/T8). |  |
| `test_sec_imm_09_legal_hold_changes_are_audited` | test_immutability.py | SEC-IMM: integridad de la evidencia, legal hold y registros append-only (SSD §3.2, §20.4, amenazas T7/T8). |  |
| `test_sec_inj_01_sqli_in_login_is_inert` | test_injection.py | SEC-INJ: inyección (SQL, control chars, parámetros de ruta) — OWASP A03, amenaza T5. | sí |
| `test_sec_inj_02_sqli_in_case_title_is_stored_literally` | test_injection.py | SEC-INJ: inyección (SQL, control chars, parámetros de ruta) — OWASP A03, amenaza T5. | sí |
| `test_sec_inj_03_sqli_in_question_and_filters` | test_injection.py | SEC-INJ: inyección (SQL, control chars, parámetros de ruta) — OWASP A03, amenaza T5. | sí |
| `test_sec_inj_04_database_intact_after_payloads` | test_injection.py | SEC-INJ: inyección (SQL, control chars, parámetros de ruta) — OWASP A03, amenaza T5. |  |
| `test_sec_inj_05_path_ids_must_be_uuids` | test_injection.py | SEC-INJ: inyección (SQL, control chars, parámetros de ruta) — OWASP A03, amenaza T5. | sí |
| `test_sec_inj_06_control_characters_rejected` | test_injection.py | SEC-INJ: inyección (SQL, control chars, parámetros de ruta) — OWASP A03, amenaza T5. | sí |
| `test_sec_inj_07_question_length_limit` | test_injection.py | SEC-INJ: inyección (SQL, control chars, parámetros de ruta) — OWASP A03, amenaza T5. |  |
| `test_sec_inj_08_validation_errors_do_not_echo_input` | test_injection.py | SEC-INJ: inyección (SQL, control chars, parámetros de ruta) — OWASP A03, amenaza T5. |  |
| `test_sec_inj_09_malformed_json_is_422_not_500` | test_injection.py | SEC-INJ: inyección (SQL, control chars, parámetros de ruta) — OWASP A03, amenaza T5. |  |
| `test_sec_jwt_01_valid_forged_with_real_secret_works` | test_jwt.py | Control positivo: el fabricador produce tokens válidos, así los negativos prueban lo que dicen. |  |
| `test_sec_jwt_02_missing_or_malformed_header` | test_jwt.py | SEC-JWT: validación de tokens (SSD §20.1, amenaza T3 — suplantación). | sí |
| `test_sec_jwt_03_alg_none_is_rejected` | test_jwt.py | SEC-JWT: validación de tokens (SSD §20.1, amenaza T3 — suplantación). |  |
| `test_sec_jwt_04_wrong_secret_is_rejected` | test_jwt.py | SEC-JWT: validación de tokens (SSD §20.1, amenaza T3 — suplantación). |  |
| `test_sec_jwt_05_algorithm_confusion_is_rejected` | test_jwt.py | SEC-JWT: validación de tokens (SSD §20.1, amenaza T3 — suplantación). |  |
| `test_sec_jwt_06_expired_token` | test_jwt.py | SEC-JWT: validación de tokens (SSD §20.1, amenaza T3 — suplantación). |  |
| `test_sec_jwt_07_wrong_or_missing_claims` | test_jwt.py | SEC-JWT: validación de tokens (SSD §20.1, amenaza T3 — suplantación). | sí |
| `test_sec_jwt_08_org_claim_forgery_cannot_cross_tenant` | test_jwt.py | Aun con la firma correcta, cambiar 'org' no da acceso: el usuario no existe bajo el RLS de otro tenant. |  |
| `test_sec_jwt_09_role_claim_is_not_trusted` | test_jwt.py | El rol del token se ignora: la autorización usa el rol vigente en la base de datos. |  |
| `test_sec_jwt_10_refresh_token_cannot_be_used_as_access` | test_jwt.py | SEC-JWT: validación de tokens (SSD §20.1, amenaza T3 — suplantación). |  |
| `test_sec_jwt_11_tampered_payload_invalidates_signature` | test_jwt.py | SEC-JWT: validación de tokens (SSD §20.1, amenaza T3 — suplantación). |  |
| `test_sec_jwt_12_token_never_contains_sensitive_data` | test_jwt.py | SEC-JWT: validación de tokens (SSD §20.1, amenaza T3 — suplantación). |  |
| `test_sec_mass_01_create_case_rejects_privileged_fields` | test_mass_assignment.py | SEC-MASS: asignación masiva (OWASP API6). Los DTO usan extra='forbid' y la revisión tiene lista blanca. | sí |
| `test_sec_mass_02_patch_case_rejects_privileged_fields` | test_mass_assignment.py | SEC-MASS: asignación masiva (OWASP API6). Los DTO usan extra='forbid' y la revisión tiene lista blanca. | sí |
| `test_sec_mass_03_review_changes_whitelist` | test_mass_assignment.py | SEC-MASS: asignación masiva (OWASP API6). Los DTO usan extra='forbid' y la revisión tiene lista blanca. | sí |
| `test_sec_mass_04_review_sql_identifier_injection_via_keys` | test_mass_assignment.py | SEC-MASS: asignación masiva (OWASP API6). Los DTO usan extra='forbid' y la revisión tiene lista blanca. |  |
| `test_sec_mass_05_member_role_is_enumerated` | test_mass_assignment.py | SEC-MASS: asignación masiva (OWASP API6). Los DTO usan extra='forbid' y la revisión tiene lista blanca. | sí |
| `test_sec_mass_06_query_rejects_unknown_fields` | test_mass_assignment.py | SEC-MASS: asignación masiva (OWASP API6). Los DTO usan extra='forbid' y la revisión tiene lista blanca. |  |
| `test_sec_ai_01_case_content_is_escaped_inside_evidence_blocks` | test_prompt_injection.py | SEC-AI: prompt injection y salidas maliciosas del modelo (SSD §20.5, §74-76, amenazas T9-T11). |  |
| `test_sec_ai_02_question_is_escaped_too` | test_prompt_injection.py | SEC-AI: prompt injection y salidas maliciosas del modelo (SSD §20.5, §74-76, amenazas T9-T11). |  |
| `test_sec_ai_03_system_prompt_declares_content_untrusted_and_locale` | test_prompt_injection.py | SEC-AI: prompt injection y salidas maliciosas del modelo (SSD §20.5, §74-76, amenazas T9-T11). |  |
| `test_sec_ai_04_citation_to_nonexistent_evidence_is_dropped` | test_prompt_injection.py | SEC-AI: prompt injection y salidas maliciosas del modelo (SSD §20.5, §74-76, amenazas T9-T11). |  |
| `test_sec_ai_05_uncited_claims_are_never_presented_as_facts` | test_prompt_injection.py | SEC-AI: prompt injection y salidas maliciosas del modelo (SSD §20.5, §74-76, amenazas T9-T11). |  |
| `test_sec_ai_06_malformed_model_output_never_500` | test_prompt_injection.py | SEC-AI: prompt injection y salidas maliciosas del modelo (SSD §20.5, §74-76, amenazas T9-T11). | sí |
| `test_sec_ai_07_model_cannot_leak_other_tenant_data` | test_prompt_injection.py | SEC-AI: prompt injection y salidas maliciosas del modelo (SSD §20.5, §74-76, amenazas T9-T11). |  |
| `test_sec_ai_08_every_answer_citation_resolves_to_real_source` | test_prompt_injection.py | SEC-AI: prompt injection y salidas maliciosas del modelo (SSD §20.5, §74-76, amenazas T9-T11). |  |
| `test_sec_ai_09_model_runs_are_recorded_for_traceability` | test_prompt_injection.py | SEC-AI: prompt injection y salidas maliciosas del modelo (SSD §20.5, §74-76, amenazas T9-T11). |  |
| `test_sec_ten_01_other_tenant_case_is_404_everywhere` | test_tenant_isolation.py | SEC-TEN: aislamiento entre organizaciones (SSD §19, §20.3, amenaza T1). | sí |
| `test_sec_ten_02_other_tenant_404_is_indistinguishable_from_nonexistent` | test_tenant_isolation.py | SEC-TEN: aislamiento entre organizaciones (SSD §19, §20.3, amenaza T1). |  |
| `test_sec_ten_03_write_operations_on_other_tenant_are_404` | test_tenant_isolation.py | SEC-TEN: aislamiento entre organizaciones (SSD §19, §20.3, amenaza T1). |  |
| `test_sec_ten_04_other_tenant_citation_and_entity_are_not_found` | test_tenant_isolation.py | SEC-TEN: aislamiento entre organizaciones (SSD §19, §20.3, amenaza T1). |  |
| `test_sec_ten_05_cannot_add_user_from_other_tenant_as_member` | test_tenant_isolation.py | SEC-TEN: aislamiento entre organizaciones (SSD §19, §20.3, amenaza T1). |  |
| `test_sec_ten_06_confidential_marker_never_reaches_other_tenant` | test_tenant_isolation.py | SEC-TEN: aislamiento entre organizaciones (SSD §19, §20.3, amenaza T1). |  |
| `test_sec_ten_07_audit_api_is_scoped_to_own_org` | test_tenant_isolation.py | SEC-TEN: aislamiento entre organizaciones (SSD §19, §20.3, amenaza T1). |  |
| `test_sec_ten_10_rls_hides_everything_without_org_context` | test_tenant_isolation.py | SEC-TEN: aislamiento entre organizaciones (SSD §19, §20.3, amenaza T1). |  |
| `test_sec_ten_11_rls_shows_only_current_org` | test_tenant_isolation.py | SEC-TEN: aislamiento entre organizaciones (SSD §19, §20.3, amenaza T1). |  |
| `test_sec_ten_12_rls_with_check_blocks_insert_into_other_org` | test_tenant_isolation.py | SEC-TEN: aislamiento entre organizaciones (SSD §19, §20.3, amenaza T1). |  |
| `test_sec_ten_13_trigger_blocks_cross_tenant_foreign_keys` | test_tenant_isolation.py | Aunque organization_id sea el propio, apuntar a un expediente de otro tenant => TENANT_MISMATCH. |  |
| `test_sec_ten_14_rls_cannot_be_bypassed_by_updating_org` | test_tenant_isolation.py | Reasignar un expediente propio a otro tenant debe fallar (WITH CHECK) — jamás se "regala" un caso. |  |
| `test_sec_ten_15_app_role_has_no_bypassrls_and_no_superuser` | test_tenant_isolation.py | SEC-TEN: aislamiento entre organizaciones (SSD §19, §20.3, amenaza T1). |  |
| `test_sec_ten_16_all_tenant_tables_force_rls` | test_tenant_isolation.py | SEC-TEN: aislamiento entre organizaciones (SSD §19, §20.3, amenaza T1). |  |
| `test_sec_ten_17_chat_tables_are_invisible_to_other_tenant` | test_tenant_isolation.py | SEC-TEN: aislamiento entre organizaciones (SSD §19, §20.3, amenaza T1). |  |
| `test_sec_ten_18_chat_with_check_blocks_insert_into_other_org` | test_tenant_isolation.py | SEC-TEN: aislamiento entre organizaciones (SSD §19, §20.3, amenaza T1). |  |
| `test_sec_ten_19_chat_triggers_block_cross_tenant_foreign_keys` | test_tenant_isolation.py | Sesión propia apuntando a expediente ajeno, y mensaje propio colgado de sesión ajena. |  |
| `test_sec_ten_20_chat_message_constraints_enforced` | test_tenant_isolation.py | role válido y attachments/citations siempre arreglos jsonb (los '@' reales del chat). |  |
| `test_sec_ten_21_chat_attachment_from_other_tenant_is_invisible` | test_tenant_isolation.py | Adjuntar con '@' un documento de otra org no filtra nada: sin tarjeta y sin contenido. |  |
| `test_sec_ten_22_chat_sessions_are_tenant_scoped` | test_tenant_isolation.py | Sesiones de chat: crear en expediente ajeno → 404; leer/enviar a sesión ajena → 404. |  |
| `test_sec_upl_01_disallowed_extensions` | test_uploads.py | SEC-UPL: carga de archivos (SSD §20.2, amenaza T6 — archivos maliciosos). | sí |
| `test_sec_upl_02_extension_content_mismatch` | test_uploads.py | SEC-UPL: carga de archivos (SSD §20.2, amenaza T6 — archivos maliciosos). |  |
| `test_sec_upl_03_declared_content_type_mismatch` | test_uploads.py | SEC-UPL: carga de archivos (SSD §20.2, amenaza T6 — archivos maliciosos). |  |
| `test_sec_upl_04_empty_file` | test_uploads.py | SEC-UPL: carga de archivos (SSD §20.2, amenaza T6 — archivos maliciosos). |  |
| `test_sec_upl_05_malware_signature_rejected_and_not_stored` | test_uploads.py | SEC-UPL: carga de archivos (SSD §20.2, amenaza T6 — archivos maliciosos). |  |
| `test_sec_upl_06_size_limit` | test_uploads.py | SEC-UPL: carga de archivos (SSD §20.2, amenaza T6 — archivos maliciosos). |  |
| `test_sec_upl_07_filename_sanitized` | test_uploads.py | SEC-UPL: carga de archivos (SSD §20.2, amenaza T6 — archivos maliciosos). | sí |
| `test_sec_upl_08_storage_key_is_content_addressed_not_user_controlled` | test_uploads.py | SEC-UPL: carga de archivos (SSD §20.2, amenaza T6 — archivos maliciosos). |  |
| `test_sec_upl_09_media_endpoint_rejects_documents_and_vice_versa` | test_uploads.py | SEC-UPL: carga de archivos (SSD §20.2, amenaza T6 — archivos maliciosos). |  |
| `test_sec_upl_10_download_verifies_integrity` | test_uploads.py | Si el objeto almacenado es alterado, la descarga falla en vez de servir evidencia manipulada. |  |

## Comportamiento: DEBE / NO DEBE — 24 funciones

| Prueba | Archivo | Qué verifica | Parametrizada |
|---|---|---|---|
| `test_must_01_preserve_originals_byte_for_byte` | test_must.py | §3.2/§132: preserva originales y calcula checksum. |  |
| `test_must_02_every_answer_claim_cites_page_or_timestamp` | test_must.py | §133: sin cita inexistente, sin fuente inexistente; documento+página o media+timestamp. |  |
| `test_must_03_can_open_source_page_and_timestamp` | test_must.py | §132: permite abrir la página fuente y el timestamp fuente. |  |
| `test_must_04_allegations_are_distinguished_from_facts` | test_must.py | §1/§13: una afirmación de parte no es un hecho; los hechos disputados conservan ambas posturas. |  |
| `test_must_05_relevant_contradictions_are_surfaced` | test_must.py | §133: una contradicción relevante no se omite; se marca para revisión humana. |  |
| `test_must_06_insufficient_evidence_is_declared_localized` | test_must.py | §20: si no hay evidencia se dice explícitamente, en el idioma del usuario. |  |
| `test_must_07_human_review_keeps_history` | test_must.py | §34/§95: la revisión no sobrescribe en silencio: versión +1, revisor y salida original conservados. |  |
| `test_must_08_low_confidence_ocr_asr_requires_review` | test_must.py | §134 riesgos 1-2: OCR/ASR bajo umbral se marca para revisión humana. |  |
| `test_must_09_timeline_is_ordered_and_source_backed` | test_must.py | §132: genera timeline; cada evento con fuente. |  |
| `test_must_10_every_error_code_and_enum_is_bilingual` | test_must.py | Requisito del proyecto: compatibilidad total es/en. |  |
| `test_must_11_every_sensitive_action_is_audited` | test_must.py | §132: registra auditoría (consultas IA, cargas, revisiones, legal hold). |  |
| `test_must_12_processing_is_idempotent` | test_must.py | §128: procesar dos veces lo mismo no duplica. |  |
| `test_not_01_no_judicial_determination_without_decision` | test_must_not.py | §13: nunca 'determinado judicialmente' sin decisión citada — ni por API ni por SQL. |  |
| `test_not_02_no_speaker_confirmation_without_party` | test_must_not.py | §134 riesgo 3: no se confirma la identidad de un hablante sin parte asociada. |  |
| `test_not_03_platform_never_decides_contradictions` | test_must_not.py | §20: toda contradicción exige revisión humana; la base de datos no permite desactivarlo. |  |
| `test_not_04_no_model_call_without_evidence` | test_must_not.py | §20: sin evidencia no se llama al modelo ni se inventa una respuesta. |  |
| `test_not_05_no_invented_citations_or_numbers` | test_must_not.py | §133: citation inexistente / dato no respaldado => la respuesta no lo presenta. |  |
| `test_not_06_no_claim_attributed_to_wrong_speaker` | test_must_not.py | §133: claim atribuido a persona incorrecta. Un claim citado en audiencia debe coincidir con el hablante resuelto. |  |
| `test_not_07_no_silent_overwrite` | test_must_not.py | §1672: una modificación no sobrescribe en silencio (bloqueo optimista). |  |
| `test_not_08_no_duplicate_evidence` | test_must_not.py | §128: el mismo archivo (contenido + nombre) no genera duplicados inconsistentes. |  |
| `test_not_09_jurisdiction_rules_do_not_leak_into_generic_domain` | test_must_not.py | §2564: la validación colombiana no contamina la jurisdicción genérica. | sí |
| `test_not_10_no_spending_beyond_case_budget` | test_must_not.py | § costos: al agotar el presupuesto de tokens del expediente no se invoca el modelo. |  |
| `test_not_11_no_obedience_to_instructions_inside_documents` | test_must_not.py | §20.5: el contenido del expediente es dato, nunca instrucción. |  |
| `test_not_12_no_deletion_of_evidence_through_api` | test_must_not.py | §3.2: los originales no se borran vía API; sólo se solicita borrado (auditado, sujeto a legal hold). |  |

## Análisis estático — 11 funciones

| Prueba | Archivo | Qué verifica | Parametrizada |
|---|---|---|---|
| `test_static_01_no_secrets_urls_or_emails_in_app_code` | test_no_hardcoded.py | STATIC: nada quemado en el código (requisito del proyecto) y coherencia de configuración. | sí |
| `test_static_02_every_setting_is_documented_in_env_example` | test_no_hardcoded.py | STATIC: nada quemado en el código (requisito del proyecto) y coherencia de configuración. |  |
| `test_static_03_settings_have_no_defaults` | test_no_hardcoded.py | Toda configuración viene del entorno: los campos de Settings no tienen valor por defecto. |  |
| `test_static_04_env_example_has_no_real_secrets` | test_no_hardcoded.py | STATIC: nada quemado en el código (requisito del proyecto) y coherencia de configuración. |  |
| `test_static_05_every_error_code_used_exists_in_both_catalogs` | test_no_hardcoded.py | STATIC: nada quemado en el código (requisito del proyecto) y coherencia de configuración. |  |
| `test_static_06_every_message_key_used_exists` | test_no_hardcoded.py | STATIC: nada quemado en el código (requisito del proyecto) y coherencia de configuración. |  |
| `test_static_07_sql_is_never_built_from_user_values` | test_no_hardcoded.py | Sólo se permiten f-strings SQL con identificadores de listas blancas internas (tablas/columnas). |  |
| `test_static_08_seed_data_contains_no_real_domains` | test_no_hardcoded.py | STATIC: nada quemado en el código (requisito del proyecto) y coherencia de configuración. |  |
| `test_static_sch_01_schema_is_valid` | test_schemas.py | STATIC: los JSON Schemas del Case Knowledge Package son válidos y los prompts están versionados. | sí |
| `test_static_sch_02_answer_schema_accepts_platform_output` | test_schemas.py | STATIC: los JSON Schemas del Case Knowledge Package son válidos y los prompts están versionados. |  |
| `test_static_sch_03_prompts_are_versioned_and_declare_untrusted_content` | test_schemas.py | STATIC: los JSON Schemas del Case Knowledge Package son válidos y los prompts están versionados. |  |
