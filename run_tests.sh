#!/bin/bash

# Set PYTHONPATH to the current directory so pytest can find the 'app' module
export PYTHONPATH=.

echo "🧪 Running Test Suite..."
echo "--------------------------------"

# Run pytest with verbose output
pytest tests/ -v

# Check if tests passed
if [ $? -eq 0 ]; then
    echo "--------------------------------"
    echo "✅ All tests passed!"
else
    echo "--------------------------------"
    echo "❌ Tests failed."
    exit 1
fi