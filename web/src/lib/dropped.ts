import type { UploadItem } from '../api/files'

/**
 * Files dropped from the desktop, folders included (walked recursively, structure kept).
 * Must be called synchronously inside the drop handler: DataTransfer items die after it returns.
 */
export function collectDropped(dataTransfer: DataTransfer): Promise<UploadItem[]> {
  const entries = Array.from(dataTransfer.items)
    .filter((item) => item.kind === 'file')
    .map((item) => item.webkitGetAsEntry())
    .filter((entry): entry is FileSystemEntry => entry !== null)

  // Browsers without the entries API: plain files only.
  if (entries.length === 0) {
    return Promise.resolve(Array.from(dataTransfer.files).map((file) => ({ path: file.name, file })))
  }
  return Promise.all(entries.map((entry) => walk(entry, ''))).then((lists) => lists.flat())
}

async function walk(entry: FileSystemEntry, prefix: string): Promise<UploadItem[]> {
  const path = prefix ? `${prefix}/${entry.name}` : entry.name
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject))
    return [{ path, file }]
  }
  const reader = (entry as FileSystemDirectoryEntry).createReader()
  const children: FileSystemEntry[] = []
  // readEntries returns results in batches (100 in Chrome) until it returns an empty one.
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject))
    if (batch.length === 0) break
    children.push(...batch)
  }
  const nested = await Promise.all(children.map((child) => walk(child, path)))
  return nested.flat()
}
