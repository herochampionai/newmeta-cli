---
name: deploy
description: Print the suggested deploy flow for the target project.
allow_plan: true
---
# Deploy helper (data-driven command)
Project: {{ args }}
Steps:
1. Check the working tree:   git status
2. Run the test suite:       python -m pytest
3. Build:                    python -m build
4. Push tag and release:     git tag vX.Y.Z && git push --tags
