"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Wand2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

type Section = "diagnosis" | "strategy" | "execution";
const SECTIONS: { value: Section; label: string }[] = [
  { value: "diagnosis", label: "Diagnosis" },
  { value: "strategy", label: "Strategy" },
  { value: "execution", label: "Execution plan" },
];

/**
 * Ask for a plain-language change to one section of the plan. The AI rewrites that section (held to the
 * same schema the Brain reads) and saves a new version; the dashboard then offers to re-run execution.
 */
export function PlanEditDialog({
  projectId,
  defaultSection = "diagnosis",
}: {
  projectId: string;
  defaultSection?: Section;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [section, setSection] = useState<Section>(defaultSection);
  const [instruction, setInstruction] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const text = instruction.trim();
    if (text.length < 3) {
      toast.error("Describe the change you want.");
      return;
    }
    setLoading(true);
    try {
      const response = await fetch(`/api/projects/${projectId}/plan/edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ section, instruction: text }),
      });
      const body = (await response.json().catch(() => null)) as { error?: string } | null;
      if (!response.ok) {
        toast.error(body?.error ?? "Could not apply the change.");
        return;
      }
      toast.success("Plan updated — a new version was saved.");
      setOpen(false);
      setInstruction("");
      router.push(`/projects/${projectId}/plan`);
      router.refresh();
    } catch {
      toast.error("Could not apply the change. Try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button variant="ghost">
            <Wand2 aria-hidden />
            Suggest a change
          </Button>
        }
      />
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>Change the plan</DialogTitle>
            <DialogDescription>
              Describe the change in plain words. The AI rewrites that part of the plan and saves it as a new
              version, keeping everything else — then you can re-run execution with it from the dashboard.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-2">
            <Label htmlFor="plan-edit-section">Which part?</Label>
            <Select items={SECTIONS} value={section} onValueChange={(value) => setSection(value as Section)}>
              <SelectTrigger id="plan-edit-section" className="w-full" aria-label="Plan section">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SECTIONS.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="plan-edit-instruction">What should change?</Label>
            <Textarea
              id="plan-edit-instruction"
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
              rows={4}
              maxLength={2000}
              placeholder="e.g. Raise the company target to 200. Remove LinkedIn as a channel. Make the diagnosis intro less formal."
              disabled={loading}
            />
            <p className="text-xs text-muted-foreground">
              This saves a <strong className="font-medium text-foreground">new plan version</strong> and does{" "}
              <strong className="font-medium text-foreground">not</strong> change the running execution — you apply it to
              execution separately from the dashboard. The current version stays in the version list. It may take up to a
              minute.
            </p>
          </div>

          <DialogFooter>
            <DialogClose
              render={
                <Button type="button" variant="outline" disabled={loading}>
                  Cancel
                </Button>
              }
            />
            <Button type="submit" disabled={loading}>
              {loading && <Loader2 className="animate-spin" aria-hidden />}
              {loading ? "Applying…" : "Apply change"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
