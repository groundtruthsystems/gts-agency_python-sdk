# Python Style Guide

Based on the Google Python Style Guide (as adopted in gts-agency), with
project-specific overrides for this SDK listed at the end. **On any conflict, the
project-specific overrides win.**

## 1. Python Language Rules
- **Imports:** Use `import x` for packages/modules. Use `from x import y` only when `y` is a submodule.
- **Exceptions:** Use built-in exception classes. Do not use bare `except:` clauses.
- **Global State:** Avoid mutable global state. Module-level constants are okay and should be `ALL_CAPS_WITH_UNDERSCORES`.
- **Comprehensions:** Use for simple cases. Avoid for complex logic where a full loop is more readable.
- **Default Argument Values:** Do not use mutable objects (like `[]` or `{}`) as default values.
- **True/False Evaluations:** Use implicit false (e.g., `if not my_list:`). Use `if foo is None:` to check for `None`.
- **Type Annotations:** Required for all production code (mypy strict).

## 2. Python Style Rules
- **Indentation:** 4 spaces per indentation level. Never use tabs.
- **Blank Lines:** Two blank lines between top-level definitions (classes, functions). One blank line between method definitions.
- **Whitespace:** Avoid extraneous whitespace. Surround binary operators with single spaces.
- **Docstrings:** Use `"""triple double quotes"""`. Every public module, function, class, and method must have a docstring.
  - **Format:** Start with a one-line summary. Include `Args:`, `Returns:`, and `Raises:` sections where signatures are not self-explanatory.
- **Strings:** Use f-strings for formatting. Use double (`"`) quotes (black default).
- **`TODO` Comments:** Use `TODO(username): Fix this.` format.
- **Imports Formatting:** Imports should be on separate lines and grouped: standard library, third-party, and your own application's imports.

## 3. Naming
- **General:** `snake_case` for modules, functions, methods, and variables.
- **Classes:** `PascalCase`.
- **Constants:** `ALL_CAPS_WITH_UNDERSCORES`.
- **Internal Use:** Use a single leading underscore (`_internal_variable`) for internal module/class members.

## 4. Main
- All executable files (e.g., example scripts) should have a `main()` function that contains the main logic, called from a `if __name__ == '__main__':` block.

## 5. Project-Specific Overrides (this SDK)

- **Line Length:** Maximum **120** characters (black configuration), not 80.
- **Tooling:** `black` for formatting and `mypy --strict` for static analysis, not pylint.
  All production code must pass both; tests (`agency_sdk/test/`) are mypy-relaxed.
- **Type syntax:** PEP 604 unions only — `X | None`, never `Optional[X]`;
  builtin generics — `dict[str, str]`, never `Dict[str, str]`. Python ≥ 3.12 is assumed.
- **Pydantic v2 only:** `BaseModel`, `ConfigDict`, `Field`, `model_dump(mode="json")`.
  CamelCase APIs map via `alias_generator=_to_camel` + `populate_by_name=True`;
  snake_case APIs use plain field names. Shared `Page` type lives in `datasets_dto.py`.
- **Delegate pattern:** each API domain is a `<domain>_client.py` / `<domain>_dto.py`
  pair under `agency_sdk/delegates/`; clients own a `_make_request` helper, errors
  propagate via `response.raise_for_status()` with no custom wrapping.
- **Query parameter abbreviations:** `o` (org), `s` (size), `p` (page), `v` (version).

**BE CONSISTENT.** When editing code, match the existing style.

*Source: [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)*
