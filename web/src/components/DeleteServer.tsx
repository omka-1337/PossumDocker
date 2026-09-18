import { IconTrash } from '@tabler/icons-react'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { useDeleteServer } from '../api/queries'
import type { Server } from '../api/types'
import { Button, Modal } from './ui'

/** Deletes the server and everything in it. Administrators only; it can't be undone. */
export function DeleteServer({ server }: { server: Server }) {
  const [confirming, setConfirming] = useState(false)
  const deleteServer = useDeleteServer()
  const navigate = useNavigate()

  return (
    <>
      <Button variant="danger" onClick={() => setConfirming(true)}>
        <IconTrash size={16} /> delete server
      </Button>

      {confirming && (
        <Modal title="delete server?" onClose={() => setConfirming(false)}>
          <p className="mb-5 text-sm text-muted">
            <span className="text-zinc-100">{server.name}</span> will be stopped and removed together with all its
            files and worlds. this can't be undone.
          </p>
          {deleteServer.error && <p className="mb-3 text-sm text-red-400">{deleteServer.error.message}</p>}
          <div className="flex gap-2">
            <Button className="flex-1" onClick={() => setConfirming(false)}>
              cancel
            </Button>
            <Button
              variant="danger"
              className="flex-1"
              disabled={deleteServer.isPending}
              onClick={() => deleteServer.mutate(server.id, { onSuccess: () => navigate('/') })}
            >
              {deleteServer.isPending ? 'deleting…' : 'delete'}
            </Button>
          </div>
        </Modal>
      )}
    </>
  )
}
