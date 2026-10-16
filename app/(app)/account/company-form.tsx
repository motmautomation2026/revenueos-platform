"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormField } from "@/components/form-field";
import { companySchema, type CompanyInput } from "@/lib/validation";
import { updateCompany } from "./actions";

export function CompanyForm({ values }: { values: CompanyInput }) {
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting, isDirty },
  } = useForm<CompanyInput>({ resolver: zodResolver(companySchema), defaultValues: values });

  const onSubmit = handleSubmit(async (input) => {
    const result = await updateCompany(input);
    if (!result.ok) {
      toast.error(result.error);
      return;
    }
    toast.success("Company details saved.");
    reset(input);
  });

  return (
    <form onSubmit={onSubmit} className="grid gap-4" noValidate>
      <div className="grid gap-4 sm:grid-cols-2">
        <FormField id="companyName" label="Company name" error={errors.companyName?.message}>
          <Input id="companyName" autoComplete="organization" {...register("companyName")} />
        </FormField>
        <FormField id="designation" label="Your designation" error={errors.designation?.message}>
          <Input id="designation" autoComplete="organization-title" {...register("designation")} />
        </FormField>
        <FormField id="phone" label="Phone" error={errors.phone?.message}>
          <Input id="phone" type="tel" autoComplete="tel" placeholder="+91 98765 43210" {...register("phone")} />
        </FormField>
        <FormField id="website" label="Website" error={errors.website?.message}>
          <Input id="website" type="url" autoComplete="url" placeholder="example.com" {...register("website")} />
        </FormField>
      </div>

      <FormField id="address" label="Address" error={errors.address?.message}>
        <Textarea id="address" rows={2} autoComplete="street-address" {...register("address")} />
      </FormField>

      <div className="grid gap-4 sm:grid-cols-3">
        <FormField id="city" label="City" error={errors.city?.message}>
          <Input id="city" autoComplete="address-level2" {...register("city")} />
        </FormField>
        <FormField id="state" label="State" error={errors.state?.message}>
          <Input id="state" autoComplete="address-level1" {...register("state")} />
        </FormField>
        <FormField id="pincode" label="PIN code" error={errors.pincode?.message}>
          <Input id="pincode" inputMode="numeric" autoComplete="postal-code" maxLength={6} {...register("pincode")} />
        </FormField>
      </div>

      <FormField id="gstin" label="GSTIN" error={errors.gstin?.message} hint="15 characters. Leave blank if not registered.">
        <Input id="gstin" autoComplete="off" maxLength={15} className="uppercase sm:max-w-xs" {...register("gstin")} />
      </FormField>

      <Button type="submit" disabled={isSubmitting || !isDirty} className="justify-self-start">
        {isSubmitting ? "Saving…" : "Save company details"}
      </Button>
    </form>
  );
}
