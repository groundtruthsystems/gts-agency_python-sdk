# Project Workflow

Adapted from the gts-agency conductor workflow for this Python SDK, and moved to the
Sebenza JSON/kanban convention on 2026-08-03: a track's plan is a schema-validated
`plan.json` (`sebenza-plan-v1`), and `tracks.json` (`sebenza-tracks-v1`) is the registry.
Statuses are `backlog` / `doing` / `blocked` / `unblocked` / `done`; keep `plan.json` and
`tracks.json` in sync on every status change. (The four tracks completed under the former
`conductor/` workspace keep their narrative `plan.md` — see `index.md`.)

## Guiding Principles

1. **The Plan is the Source of Truth:** All work must be tracked in `plan.json`
2. **The Tech Stack is Deliberate:** Changes to the tech stack must be documented in `tech-stack.md` *before* implementation
3. **Test-Driven Development:** Write unit tests before implementing functionality
4. **High Code Coverage:** Aim for >80% code coverage for all modules
5. **User Experience First:** Every decision should prioritize the SDK consumer's experience
6. **Non-Interactive & CI-Aware:** Prefer non-interactive commands. Use `CI=true` for watch-mode tools (tests, linters) to ensure single execution.

## Task Workflow

All tasks follow a strict lifecycle:

### Standard Task Workflow

1. **Select Task:** Choose the next available task from `plan.json` in sequential order

2. **Mark In Progress:** Before beginning work, edit `plan.json` and change the task's `status`
   from `"backlog"` to `"doing"` (and its phase, if this is the phase's first task); mirror the
   phase status into that track's `phases_summary` in `tracks.json`

3. **Write Failing Tests (Red Phase):**
   - Create a new test file for the feature or bug fix.
   - Write one or more unit tests that clearly define the expected behavior and acceptance criteria for the task.
   - **CRITICAL:** Run the tests and confirm that they fail as expected. This is the "Red" phase of TDD. Do not proceed until you have failing tests.

4. **Implement to Pass Tests (Green Phase):**
   - Write the minimum amount of application code necessary to make the failing tests pass.
   - Run the test suite again and confirm that all tests now pass. This is the "Green" phase.

5. **Refactor (Optional but Recommended):**
   - With the safety of passing tests, refactor the implementation code and the test code to improve clarity, remove duplication, and enhance performance without changing the external behavior.
   - Rerun tests to ensure they still pass after refactoring.

6. **Verify Coverage:** Run coverage reports:
   ```bash
   pytest --cov=agency_sdk --cov-report=term-missing
   ```
   Target: >80% coverage for new code.

7. **Document Deviations:** If implementation differs from tech stack:
   - **STOP** implementation
   - Update `tech-stack.md` with new design
   - Add dated note explaining the change
   - Resume implementation

8. **Commit Code Changes:**
   - Stage all code changes related to the task.
   - Propose a clear, concise commit message e.g., `feat(files): Add files delegate client`.
   - Perform the commit.

9. **Attach Task Summary with Git Notes:**
   - **Step 9.1: Get Commit Hash:** Obtain the hash of the *just-completed commit* (`git log -1 --format="%H"`).
   - **Step 9.2: Draft Note Content:** Create a detailed summary for the completed task. This should include the task name, a summary of changes, a list of all created/modified files, and the core "why" for the change.
   - **Step 9.3: Attach Note:** Use the `git notes` command to attach the summary to the commit.
     ```bash
     git notes add -m "<note content>" <commit_hash>
     ```

10. **Get and Record Task Commit SHA:**
    - **Step 10.1: Update Plan:** Read `plan.json`, find the completed task, set its `status` to
      `"done"`, and set its `commit_sha` to the first 7 characters of the *just-completed commit's*
      hash.
    - **Step 10.2: Write Plan:** Write the updated JSON back to `plan.json`.
    - **Step 10.3: Sync Registry:** Update this track's `progress` (`completed_tasks`,
      `percentage`) in `tracks.json`, and refresh its `updated_at`.

11. **Commit Plan Update:**
    - **Action:** Stage the modified `plan.json` and `tracks.json`.
    - **Action:** Commit this change with a descriptive message (e.g., `sebenza(plan): Mark task 'Create files DTOs' as complete`).

### Phase Completion Verification and Checkpointing Protocol

**Trigger:** This protocol is executed immediately after a task is completed that also concludes a phase in `plan.json`.

1.  **Announce Protocol Start:** Inform the user that the phase is complete and the verification and checkpointing protocol has begun.

2.  **Ensure Test Coverage for Phase Changes:**
    -   **Step 2.1: Determine Phase Scope:** To identify the files changed in this phase, you must first find the starting point. Read `plan.json` to find the *previous* phase's `checkpoint_sha`. If no previous checkpoint exists, the scope is all changes since the first commit.
    -   **Step 2.2: List Changed Files:** Execute `git diff --name-only <previous_checkpoint_sha> HEAD` to get a precise list of all files modified during this phase.
    -   **Step 2.3: Verify and Create Tests:** For each file in the list:
        -   **CRITICAL:** First, check its extension. Exclude non-code files (e.g., `.json`, `.md`, `.yaml`).
        -   For each remaining code file, verify a corresponding test file exists.
        -   If a test file is missing, you **must** create one. Before writing the test, **first, analyze other test files in the repository to determine the correct naming convention and testing style.** The new tests **must** validate the functionality described in this phase's tasks (`plan.json`).

3.  **Execute Automated Tests with Proactive Debugging:**
    -   Before execution, you **must** announce the exact shell command you will use to run the tests.
    -   **Example Announcement:** "I will now run the automated test suite to verify the phase. **Command:** `pytest agency_sdk/test/ -v`"
    -   Execute the announced command.
    -   If tests fail, you **must** inform the user and begin debugging. You may attempt to propose a fix a **maximum of two times**. If the tests still fail after your second proposed fix, you **must stop**, report the persistent failure, and ask the user for guidance.

4.  **Propose a Detailed, Actionable Manual Verification Plan:**
    -   **CRITICAL:** To generate the plan, first analyze `product.md`, `product-guidelines.md`, and `plan.json` to determine the user-facing goals of the completed phase.
    -   You **must** generate a step-by-step plan that walks the user through the verification process, including any necessary commands and specific, expected outcomes.
    -   Example format for this SDK:

        ```
        The automated tests have passed. For manual verification, please follow these steps:

        **Manual Verification Steps:**
        1.  **Export the environment variables:** `AGENCY_AUTH_URL`, `AGENCY_API_URL`, `AGENCY_ORG_ID`, `AGENCY_CLIENT_ID`, `AGENCY_CLIENT_SECRET`
        2.  **Execute the example script:** `python examples/quick_files.py`
        3.  **Confirm that you see:** every lifecycle step printed with PASS and exit code 0.
        ```

5.  **Await Explicit User Feedback:**
    -   After presenting the detailed plan, ask the user for confirmation: "**Does this meet your expectations? Please confirm with yes or provide feedback on what needs to be changed.**"
    -   **PAUSE** and await the user's response. Do not proceed without an explicit yes or confirmation.

6.  **Create Checkpoint Commit:**
    -   Stage all changes. If no changes occurred in this step, proceed with an empty commit.
    -   Perform the commit with a clear and concise message (e.g., `sebenza(checkpoint): Checkpoint end of Phase X`).

7.  **Attach Auditable Verification Report using Git Notes:**
    -   **Step 7.1: Draft Note Content:** Create a detailed verification report including the automated test command, the manual verification steps, and the user's confirmation.
    -   **Step 7.2: Attach Note:** Use the `git notes` command and the full commit hash from the previous step to attach the full report to the checkpoint commit.

8.  **Get and Record Phase Checkpoint SHA:**
    -   **Step 8.1: Get Commit Hash:** Obtain the hash of the *just-created checkpoint commit* (`git log -1 --format="%H"`).
    -   **Step 8.2: Update Plan:** Read `plan.json`, set the completed phase's `status` to `"done"`
        and its `checkpoint_sha` to the first 7 characters of the commit hash.
    -   **Step 8.3: Write Plan:** Write the updated JSON back to `plan.json`, and mirror the phase
        status into `tracks.json` (`phases_summary` + `progress` + `updated_at`).

9. **Commit Plan Update:**
    - **Action:** Stage the modified `plan.json` and `tracks.json`.
    - **Action:** Commit this change with a descriptive message following the format `sebenza(plan): Mark phase '<PHASE NAME>' as complete`.

10. **Announce Completion:** Inform the user that the phase is complete and the checkpoint has been created, with the detailed verification report attached as a git note.

### Quality Gates

Before marking any task complete, verify:

- [ ] All tests pass
- [ ] Code coverage meets requirements (>80%)
- [ ] Code follows project's code style guidelines (as defined in `code_styleguides/`)
- [ ] All public functions/methods are documented (docstrings)
- [ ] Type safety is enforced (`mypy agency_sdk/` strict passes)
- [ ] No linting or static analysis errors (`black --check`, `bandit -r agency_sdk/`)
- [ ] Documentation updated if needed (README, CLAUDE.md per the Delegate Delivery Checklist)
- [ ] No security vulnerabilities introduced

## Development Commands

### Setup
```bash
pip install -e ".[dev]"
```

### Daily Development
```bash
pytest agency_sdk/test/ -v        # run tests
mypy agency_sdk/                  # strict type checking
black agency_sdk/ examples/      # format
```

### Before Committing
```bash
black --check agency_sdk/ examples/
mypy agency_sdk/
pytest agency_sdk/test/
bandit -r agency_sdk/ -x agency_sdk/test   # CI parity; tests excluded (B101 asserts are expected in pytest)
```

## Testing Requirements

### Unit Testing
- Every module must have corresponding tests in `agency_sdk/test/`.
- Offline by default: HTTP interactions are stubbed by monkeypatching `requests`;
  no network access in unit tests.
- Test both success and failure cases (including client-side `ValueError` paths).

### Integration / E2E Testing
- End-to-end verification runs the example scripts against a real control plane
  (dev environment or local stack) with the `AGENCY_*` environment variables set.
- E2E scripts must be self-verifying (assertions, non-zero exit on failure) and
  idempotent (unique resource names, unconditional cleanup).

## Commit Guidelines

### Message Format
```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

### Types
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation only
- `style`: Formatting, missing semicolons, etc.
- `refactor`: Code change that neither fixes a bug nor adds a feature
- `test`: Adding missing tests
- `chore`: Maintenance tasks

### Examples
```bash
git commit -m "feat(files): Add files delegate client with gtsf:// resolution"
git commit -m "test(files): Add offline protocol tests for upload"
git commit -m "fix(rules): Remove stray debug print from execute"
```

## Definition of Done

A task is complete when:

1. All code implemented to specification
2. Unit tests written and passing
3. Code coverage meets project requirements
4. Documentation complete (if applicable)
5. Code passes all configured linting and static analysis checks
6. Implementation notes added to the task's `description` in `plan.json`
7. Changes committed with proper message
8. Git note with task summary attached to the commit

## Release Workflow

### Pre-Release Checklist
- [ ] All tests passing
- [ ] Coverage >80%
- [ ] No linting / static analysis errors
- [ ] Version bumped in `pyproject.toml`
- [ ] README and examples up to date

### Release Steps
1. Merge to `main`
2. Tag release `v<version>` and push the tag
3. GitHub Actions builds, runs the bandit security gate, and publishes to PyPI
   via OIDC trusted publishing
4. Verify the package installs from PyPI

## Security Procedures

### Leaked Credential
1. Rotate/revoke the credential at the issuer immediately (history rewrite is not sufficient)
2. Replace with placeholder or env-var-only usage as permitted by `product-guidelines.md`
3. Document the incident

## Continuous Improvement

- Review workflow regularly; update based on pain points
- Document lessons learned
- Keep things simple and maintainable
