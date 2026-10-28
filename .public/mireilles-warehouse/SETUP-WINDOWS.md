# Set up a Windows computer

Do this once per computer.

1. Open **Terminal** (or PowerShell) from the Start menu and run these one at a time, pressing Enter after each. Say yes to any prompts.
   - `winget install Git.Git` (Git keeps track of your files)
   - `winget install Microsoft.VisualStudioCode` (the editor)
   - `winget install OpenJS.NodeJS.LTS` (Node.js and npm, for your projects)
   - Optional: `winget install GitHub.cli`, then `gh auth login` and follow the prompts
2. Close Terminal and restart the computer.
3. Make a GitHub account at github.com if you don't have one, and send Marc your username so he can give you access.
4. Open VS Code. Click the person icon at the bottom-left and choose **Sign in with GitHub**.
5. Press `Ctrl+Shift+P`, type **Git: Clone**, press Enter, and paste `https://github.com/mchdeore/CRAFT.git`. Pick a folder such as Documents, then press **Open**.
6. You're done. Each time you start, press **Sync Changes** in Source Control (or the circular arrows at the bottom-left) to get the latest, then follow [README.md](README.md). Your ideas go in `.public/mireilles-warehouse/`.
