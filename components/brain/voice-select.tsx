"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { VOICE_LANGUAGES } from "@/lib/brain/voices";
import { saveAgentVoice } from "@/app/(app)/projects/[id]/execution/actions";

/**
 * Pick the AI calling voice. The default is pre-selected; changing it and saving applies to the next call
 * campaign (a running one keeps its voice). Grouped by language, since a voice is valid only for its own.
 */
export function VoiceSelect({ projectId, current }: { projectId: string; current: string }) {
  const router = useRouter();
  const [value, setValue] = useState(current);
  const [pending, start] = useTransition();
  const changed = value !== current;

  const save = () =>
    start(async () => {
      const result = await saveAgentVoice({ projectId, voiceId: value });
      if (result.ok) {
        toast.success(result.message);
        router.refresh();
      } else {
        toast.error(result.error);
      }
    });

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select value={value} onValueChange={(v) => v && setValue(v)} disabled={pending}>
        <SelectTrigger size="sm" className="w-64">
          <SelectValue placeholder="Choose a voice" />
        </SelectTrigger>
        <SelectContent>
          {VOICE_LANGUAGES.map((language) => (
            <SelectGroup key={language.language}>
              <SelectLabel>{language.label}</SelectLabel>
              {language.voices.map((voice) => (
                <SelectItem key={voice.id} value={voice.id}>
                  {voice.name} · {voice.gender === "female" ? "Female" : "Male"}
                </SelectItem>
              ))}
            </SelectGroup>
          ))}
        </SelectContent>
      </Select>
      {changed && (
        <Button size="sm" onClick={save} disabled={pending}>
          {pending ? "Saving…" : "Save voice"}
        </Button>
      )}
    </div>
  );
}
