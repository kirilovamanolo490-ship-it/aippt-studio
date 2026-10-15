"""Search the local PPT collection by occasion, page role, and visual style.

Run inside a Work with ``scripts/with-studio-runtime.py`` so the Studio Python
and its current search implementation are selected consistently.
"""

from aippt_studio.template_library import main


if __name__ == "__main__":
    raise SystemExit(main())
