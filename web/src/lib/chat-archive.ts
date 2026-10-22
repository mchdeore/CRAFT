export function confirmChatArchive(archive: () => void): void {
  if (window.confirm("Archive this chat? This deletes its workspace files. Restoring the chat will not restore those files.")) {
    archive();
  }
}
