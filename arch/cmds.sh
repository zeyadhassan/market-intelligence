(.venv) PS C:\Users\zehassan\source\repos\market-intelligence> .\run.cmd --no-browser
Configuration ready: C:\Users\zehassan\source\repos\market-intelligence\deploy\app.env

Checking the model gateways ...
Checking OpenAI-compatible chat endpoint ...
Chat endpoint OK
Checking NVIDIA NIM embedding endpoint ...
2026-09-01 15:26:26 [info     ] embed.start                    component=retrieval.embedders.openai_compatible model=nvidia/llama-3.2-nv-embedqa-1b-v2 n_texts=1
2026-09-01 15:26:26 [info     ] embed.done                     component=retrieval.embedders.openai_compatible n_texts=1 total_tokens=5
NVIDIA NIM embedding endpoint OK (2048 dimensions)

Starting the product ...
Local product configuration is complete.
mode: shadow
access: built-in local analyst
models: extraction, reasoning, embedding, reranker, entailment
>>>> Executing external compose provider "C:\\Users\\zehassan\\source\\repos\\market-intelligence\\.venv\\Scripts\\podman-compose.EXE". Please see podman-compose(1) for how to disable this message. <<<<

9b05db12a35460fff0587e009f9326e414a53e9484555547f96d214a2ba98ef7
d3238814e371a990ab3d08a8e9b13936953e448590a6648233270a2e3e8fcf75
8ccf5ebc4812295e1cc59ad9ce2fa9f1d8a4d167cbb8198c716f09d5a848cfec
68a559fad51a8cc09f21f3572bfdc2a86fdc1a7e117d7e22e3a2dbb356a8e8ee
771b2590699b253e6905c2503c92446fb33b8ad0cce4733bdb363ef0259a6246
deploy_neo4j_1
deploy_mailpit_1
applied 0001 init.sql sha256=959eb3a73752
applied 0002 0002_intelligence_ledger.sql sha256=5d1836ac0b7b
applied 0003 0003_indexed_hybrid_retrieval.sql sha256=ca9f0598dcc3
applied 0004 0004_replayable_ingestion.sql sha256=84520c47564f
applied 0005 0005_analyst_api.sql sha256=4e23cfa35ab2
applied 0006 0006_model_registry.sql sha256=3b944dd3e22d
applied 0007 0007_source_operations.sql sha256=68b3300aa338
applied 0008 0008_entity_intelligence.sql sha256=693b1b04a55c
applied 0009 0009_document_entity_link.sql sha256=a4778ab63d83
applied 0010 0010_disable_noncoverage_feeds.sql sha256=8b4b5ce6df14
applied 0011 0011_audit_empty_reads.sql sha256=4b63e4bee291
applied 0012 0012_document_dedupe_window.sql sha256=e76923464449
applied 0013 0013_resolution_queue_reason.sql sha256=f69a82d9e941
applied 0014 0014_agentic_analysis_state.sql sha256=882df7b3fe76
applied 0015 0015_factual_coverage_and_entailment.sql sha256=e8187d3d2d3c
applied 0016 0016_unified_analysis_completion.sql sha256=51a511f526f7
applied 0017 0017_outbox_worker_leases.sql sha256=89d17fa43bb5
applied 0018 0018_multilingual_retrieval_normalization.sql sha256=0fdb9a26ad64
applied 0019 0019_model_call_outcomes.sql sha256=0f16ba542ab1
applied 0020 0020_remove_live_poc_mode.sql sha256=3a1cfc2442c5
applied 0021 0021_signal_reconfirmation.sql sha256=ec620a4fac0a
applied 0022 0022_developer_mvp_runtime.sql sha256=a7a5a92423d3
applied 0023 0023_nomic_embedding_dimension.sql sha256=ea33d22704b2
applied 0024 0024_nvidia_embedding_dimension.sql sha256=80b6d20bc8b7
>>>> Executing external compose provider "C:\\Users\\zehassan\\source\\repos\\market-intelligence\\.venv\\Scripts\\podman-compose.EXE". Please see podman-compose(1) for how to disable this message. <<<<

8ccf5ebc4812295e1cc59ad9ce2fa9f1d8a4d167cbb8198c716f09d5a848cfec
9b05db12a35460fff0587e009f9326e414a53e9484555547f96d214a2ba98ef7
d3238814e371a990ab3d08a8e9b13936953e448590a6648233270a2e3e8fcf75
Error: no Containerfile or Dockerfile specified or found in context directory, C:\Users\zehassan\source\repos\market-intelligence: The system cannot find the file specified.
Error: no Containerfile or Dockerfile specified or found in context directory, C:\Users\zehassan\source\repos\market-intelligence: The system cannot find the file specified.
Error: no Containerfile or Dockerfile specified or found in context directory, C:\Users\zehassan\source\repos\market-intelligence: The system cannot find the file specified.
Error: no Containerfile or Dockerfile specified or found in context directory, C:\Users\zehassan\source\repos\market-intelligence: The system cannot find the file specified.
Error: no Containerfile or Dockerfile specified or found in context directory, C:\Users\zehassan\source\repos\market-intelligence: The system cannot find the file specified.
Error: no Containerfile or Dockerfile specified or found in context directory, C:\Users\zehassan\source\repos\market-intelligence: The system cannot find the file specified.
Error: no Containerfile or Dockerfile specified or found in context directory, C:\Users\zehassan\source\repos\market-intelligence: The system cannot find the file specified.
ERROR:podman_compose:Build command failed
ERROR:podman_compose:Prepare images failed
Error: executing C:\Users\zehassan\source\repos\market-intelligence\.venv\Scripts\podman-compose.EXE --file C:\Users\zehassan\source\repos\market-intelligence\deploy\compose.yml --env-file C:\Users\zehassan\source\repos\market-intelligence\deploy\app.env --profile app up --detach --build: exit status 125
podman infrastructure error: Command '('C:\\Users\\zehassan\\AppData\\Local\\Programs\\Podman\\podman.EXE', 'compose', '--file', 'C:\\Users\\zehassan\\source\\repos\\market-intelligence\\deploy\\compose.yml', '--env-file', 'C:\\Users\\zehassan\\source\\repos\\market-intelligence\\deploy\\app.env', '--profile', 'app', 'up', '--detach', '--build')' returned non-zero exit status 125.
product startup error: Command '('C:\\Users\\zehassan\\source\\repos\\market-intelligence\\.venv\\Scripts\\python.exe', 'C:\\Users\\zehassan\\source\\repos\\market-intelligence\\deploy\\podman_infra.py', 'app-up')' returned non-zero exit status 1.
(.venv) PS C:\Users\zehassan\source\repos\market-intelligence> 