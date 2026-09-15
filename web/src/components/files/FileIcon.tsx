import {
  IconFileCode,
  IconFileText,
  IconFileTypeZip,
  IconFile,
  IconFolderFilled,
  IconLink,
  IconPhoto,
  IconBox,
  IconDatabase,
  type Icon,
} from '@tabler/icons-react'
import { extension, type FileEntry } from '../../api/files'

const byExtension: Record<string, [Icon, string]> = {
  zip: [IconFileTypeZip, 'text-amber-400'],
  gz: [IconFileTypeZip, 'text-amber-400'],
  tar: [IconFileTypeZip, 'text-amber-400'],
  jar: [IconBox, 'text-orange-400'], // plugins and mods
  json: [IconFileCode, 'text-emerald-400'],
  yml: [IconFileCode, 'text-emerald-400'],
  yaml: [IconFileCode, 'text-emerald-400'],
  toml: [IconFileCode, 'text-emerald-400'],
  properties: [IconFileCode, 'text-emerald-400'],
  cfg: [IconFileCode, 'text-emerald-400'],
  ini: [IconFileCode, 'text-emerald-400'],
  conf: [IconFileCode, 'text-emerald-400'],
  txt: [IconFileText, 'text-zinc-300'],
  log: [IconFileText, 'text-zinc-400'],
  md: [IconFileText, 'text-zinc-300'],
  png: [IconPhoto, 'text-fuchsia-400'],
  jpg: [IconPhoto, 'text-fuchsia-400'],
  dat: [IconDatabase, 'text-sky-300'],
  mca: [IconDatabase, 'text-sky-300'],
  db: [IconDatabase, 'text-sky-300'],
}

export function FileIcon({ entry, size }: { entry: FileEntry; size: number }) {
  if (entry.type === 'dir') return <IconFolderFilled size={size} className="text-sky-400" />
  if (entry.type === 'symlink') return <IconLink size={size} stroke={1.5} className="text-zinc-400" />
  const [Component, color] = byExtension[extension(entry.name)] ?? [IconFile, 'text-zinc-400']
  return <Component size={size} stroke={1.3} className={color} />
}
