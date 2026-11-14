# Start here

1. **user.yaml**: set your `display_name`, `headline` and `location`. Put your email and phone in `.env` (never in these files).
2. **base.yaml**: replace the `example-` entries with your own roles and bullets. Mark each accurate one `status: confirmed`.
3. **Add a job**: `rb new-job --company "Company name" --title "Job title"`, then paste the posting into `jobs/<job-id>/posting.md`.
4. **Render**: `rb render public --preview` for a first look, or `rb render <job-id>` once your AI agent has tailored it (`rb brief <job-id>`).
5. **Open the tracker** at http://localhost:8000 to see every application and its history.

Problems with any file are listed in `SYNC-STATUS.md`. Comments in each file explain every field.
