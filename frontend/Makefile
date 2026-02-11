build-FittedFrontend:
	mkdir -p $(ARTIFACTS_DIR)
	pip install --target $(ARTIFACTS_DIR) -r requirements.txt
	cp app.py $(ARTIFACTS_DIR)
	if [ -d "assets" ]; then cp -r assets $(ARTIFACTS_DIR)/assets; fi
