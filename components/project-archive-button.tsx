"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Archive, ArchiveRestore, Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { archiveProject, restoreProject } from "@/app/(app)/projects/actions";

/** Archive an active project (after confirming), or restore an archived one. */
export function ProjectArchiveButton({
  projectId,
  customerName,
  archived,
}: {
  projectId: string;
  customerName: string;
  archived: boolean;
}) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setConfirming(false);
    setBusy(true);
    try {
      const result = await (archived ? restoreProject(projectId) : archiveProject(projectId));
      if (!result.ok) {
        toast.error(result.error);
        return;
      }
      toast.success(archived ? `${customerName} is active again.` : `${customerName} was archived.`);
      if (archived) router.refresh();
      else router.push("/projects");
    } catch {
      toast.error("That could not be saved. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button variant="outline" size="sm" disabled={busy} onClick={() => (archived ? void run() : setConfirming(true))}>
        {busy ? (
          <Loader2 className="animate-spin" aria-hidden />
        ) : archived ? (
          <ArchiveRestore aria-hidden />
        ) : (
          <Archive aria-hidden />
        )}
        {archived ? "Restore" : "Archive"}
      </Button>

      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Archive {customerName}?</AlertDialogTitle>
            <AlertDialogDescription>
              It moves out of your Projects list into Archived. Nothing is deleted: the documents, checklist, plan and
              results all stay, and you can restore it at any time.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button onClick={() => void run()}>Archive</Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
