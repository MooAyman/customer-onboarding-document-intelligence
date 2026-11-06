"""Case scorecard entrypoint.

Verified pairs only. Delegates to eval_cases.py. Does not filename-pair new
images and does not call process_id_pair on unpaired documents.
"""

from __future__ import annotations

from eval_cases import main

if __name__ == "__main__":
    main()
