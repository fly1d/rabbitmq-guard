.PHONY: verify smoke integration serve

verify:
	PYTHONPATH=src python3 -m unittest discover -s tests -v
	PYTHONPATH=src python3 -m compileall -q src tests scripts lab
	node --check src/rabbitmq_guard/web/app.js

smoke:
	PYTHONPATH=src python3 scripts/smoke_test.py

integration:
	RABBITMQ_URL=http://127.0.0.1:15673 RABBITMQ_PASSWORD=guard-local-only PYTHONPATH=src python3 scripts/live_integration_test.py

serve:
	PYTHONPATH=src python3 -m rabbitmq_guard serve --enable-live --port 8788
