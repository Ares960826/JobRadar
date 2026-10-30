# JobRadar — development repository

JobRadar is a job-search framework made of two agent skills (`jobradar`, stage 1: search and hard
elimination; `potential-position`, stage 2: grading, verification, Feishu workbench, application
tracking) plus their scripts, configuration and tests.

## Product invariants

The block below is part of the product. It must stay identical in both `SKILL.md` files and
here (`system/skill/potential-position/scripts/test_position.py` checks it). Change all three
together.

## Invariants (same as in both skills)

1. A human submits every application, even when the user says "go ahead" or "我同意". Stop before any submit, send, upload, recruiter message, "next step" or declaration that could file one; login, captcha and declarations belong to the user.
2. The instance's `Profile/` (hard conditions, career profile, self-description) holds only what the user chose to share with the agent: read it directly. Résumés, identity documents and other personal files are read only through the user's own privacy tooling or with their explicit permission for that file. Profile text never goes to Feishu, and career-profile aspirations are never résumé facts.
3. Permanent IDs (`JOB-000001`, or `LEAD-YYYYMMDD-NNN` for direction leads) are the only record identity. Never renumber, delete rows to eliminate, or overwrite 用户决定 / 本人备注 / 父记录.
4. Stay inside the project, `~/.config/jobradar/` and `~/.local/state/jobradar/`. Never write to another host's home (`~/.codex`, `~/.config/opencode`, `~/.claude`, `~/.deepseek`) or read its session logs.
5. No global installs (`npm -g`, `brew`, `pip` outside a project venv), global proxy/VPN, hooks or schedules without the user's explicit approval in this session.
6. Do only what the user asked in this turn. Name your mode before acting; anything outside it needs the user's go-ahead.
7. Application or interview status comes only from the user's own report or a submission receipt, never from table rows or finished materials.
8. Unknown stays unknown. Never invent a URL, deadline, quota or eligibility, and never claim a tool or capability you have not observed in this session.
9. Temporary files go in the run's `_support/` or `system/validation/<run>/`.
10. Finish with the stage's `closeout` command and quote its receipt. Without a passing receipt, report "not complete" and list what failed.

## Development rules

1. **No real job-search runs from this repository.** Develop against synthetic fixtures in temp
   directories, as the existing tests do. No live Feishu writes, no portal browsing, no
   recruiter contact.
2. **No personal data in git.** A user's data belongs in their own instance directory, never here.
   Fixtures and examples use fictional employers.
3. No global installs (`npm -g`, `brew`, `pip` outside a project venv), hooks or schedules
   without the user's approval in the current session.
4. Commit in small, separate commits with clear messages.

## Tests

```bash
python3 system/skill/jobradar/scripts/test_radar.py
python3 system/skill/potential-position/scripts/test_position.py
python3 system/tests/test_initial_filter.py
python3 system/tests/test_feishu_source.py
```
