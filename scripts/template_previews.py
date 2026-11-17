#!/usr/bin/env python3
"""Make the resume template preview images in docs/images/templates/ from the fictional example user.

Runs inside the Docker image, which has the app and WeasyPrint; it also needs pypdfium2 to turn the
first PDF page into a PNG. From the repo folder:

    docker build -t resume-builder:ci .
    docker run --rm -v "$PWD/docs/images/templates:/previews" -v "$PWD/scripts:/scripts:ro" \\
      --entrypoint sh resume-builder:ci -c "pip install --user -q pypdfium2 && cd /app && python /scripts/template_previews.py /previews"

On Windows PowerShell, use "${PWD}" in place of "$PWD".

It renders every installed resume template (newest version), in both variants, for the example user
in a temporary folder, and writes <template>.png and <template>-ats.png. Only the example user's
fictional data is ever used.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

USER = "alex-morgan"
JOB = "2026-11-02-northwind-labs-platform-engineering"


def main(out: Path, example: Path) -> None:
    tmp = Path(tempfile.mkdtemp())
    for name in ("data", "db", "output", "backups"):
        (tmp / name).mkdir()
    os.environ.update({"RB_DATA_ROOT": str(tmp / "data"), "RB_DB_ROOT": str(tmp / "db"),
                       "RB_OUTPUT_ROOT": str(tmp / "output"), "RB_BACKUP_ROOT": str(tmp / "backups"),
                       "RB_CONTACT_EMAIL": "alex@example.com", "RB_CONTACT_PHONE": "(555) 010-0199"})
    os.environ.pop("RB_DEFAULT_USER", None)

    import pypdfium2
    from resume_builder import services, sync_ops  # noqa: F401  (registers the operations)
    from resume_builder.config import load_settings
    from resume_builder.operations import invoke
    from resume_builder.security import RequestContext
    from resume_builder.services.render import export
    from resume_builder.space import App

    shutil.copytree(example, tmp / "data" / USER, ignore=shutil.ignore_patterns(".schemas", "SYNC-STATUS.md"))
    app = App(load_settings())
    sync_ops.bootstrap(app)
    space, owner = app.space(USER), RequestContext.owner(USER)
    out.mkdir(parents=True, exist_ok=True)
    for t in invoke(app, owner, "templates_list", {})["templates"]:
        for variant in ("styled", "ats"):
            sid = invoke(app, owner, "render", {"job_key": JOB, "export": None, "template": t["name"],
                                                "variant": variant})["snapshot_id"]
            pdf, _ = export(space, sid, "pdf")
            page = pypdfium2.PdfDocument(pdf)[0]
            image = page.render(scale=1.25).to_pil()
            target = out / f"{t['name']}{'-ats' if variant == 'ats' else ''}.png"
            image.save(target, optimize=True)
            print(target)
    app.close()
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "docs/images/templates"),
         Path(sys.argv[2] if len(sys.argv) > 2 else "examples/sample-user/alex-morgan"))
