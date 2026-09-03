It uses SAMA—the source already proven healthy—and completely ignores the other 11 sources during the smoke test.
On the server:
git pull origin main
python deploy/product.py --configure-only
Confirm deploy\app.env contains exactly once:
FI_INTEL_COVERAGE_REQUIRED_SOURCE_IDS=sa_sama_news
For a guaranteed clean test, remove the old failed test database. This deletes current local Podman test data:
python deploy/podman_infra.py reset --confirm RESET
python deploy/podman_infra.py app-up
python deploy/podman_infra.py source-check
The source check should now report:
source_count: 1
polled_source_ids: ["sa_sama_news"]
failed_source_ids: []
Wait about 30 seconds for projection/extraction, then open the application and click Refresh analysis:
http://127.0.0.1:8000/
Use your configured FI_INTEL_API_HOST_PORT if different.
If it still holds:
python deploy/podman_infra.py logs --no-follow --tail 500
When ready for all real sources again, change:
FI_INTEL_COVERAGE_REQUIRED_SOURCE_IDS=