# Intake folder

Use this folder when a job posting's link can't be read by the app, for example because the site needs you to sign in or only shows the posting with JavaScript.

1. Open the posting in your browser, select the whole posting text and copy it.
2. Paste it into a new text file here, for example `intake/acme-platform-engineer.txt`, and save it as UTF-8.
3. Run `rb new-job --file intake/acme-platform-engineer.txt <link to the posting>` (the link is optional; it's recorded as the posting's link).

The app reads a file here only when you run that command. It never changes, moves or deletes your files. Once a job has been created from a file, `SYNC-STATUS.md` says so, and you can delete the file: the posting is kept with the job.
