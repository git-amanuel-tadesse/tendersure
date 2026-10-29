"""The API's OpenAPI document, written the same way every time (sorted keys),
so packages/api-client/openapi.json changes only when the API does.

    python -m tendersure_api.contract [out.json]
"""

import json
import sys
import tempfile

from .app import create_app


def document() -> dict:
    with tempfile.TemporaryDirectory() as d:          # no workspace or job folder of the user's is touched
        return create_app(web_dist=None, workspaces=d).api_doc


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    text = json.dumps(document(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if argv:
        with open(argv[0], "w") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
