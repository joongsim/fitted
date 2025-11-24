.PHONY: build-FittedApi

build-FittedApi:
	mkdir -p $(ARTIFACTS_DIR)
	pip install --target $(ARTIFACTS_DIR) -r lambda_requirements.txt
	cp -r app $(ARTIFACTS_DIR)