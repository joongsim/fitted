.PHONY: build-FittedApi build-FittedFrontend

build-FittedApi:
	mkdir -p $(ARTIFACTS_DIR)
	pip install --target $(ARTIFACTS_DIR) -r lambda_requirements.txt
	cp -r app $(ARTIFACTS_DIR)

build-FittedFrontend:
	mkdir -p $(ARTIFACTS_DIR)
	pip install --target $(ARTIFACTS_DIR) -r frontend/requirements.txt
	cp frontend/app.py $(ARTIFACTS_DIR)
	if [ -d "frontend/assets" ]; then cp -r frontend/assets $(ARTIFACTS_DIR)/assets; fi
