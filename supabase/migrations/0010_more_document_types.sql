-- More kinds of customer documents on the Inputs page.
--
-- Besides the BD proposal and handover, a customer usually has a company profile, a product
-- catalogue, brochures, a presentation, case studies and emails they already send. Each kind is
-- stored with its own type, and the agents are told how far to trust it (lib/files/rules.ts):
-- agreed facts, the customer's own material, or style examples only.
--
-- Adding values to an enum cannot be undone without recreating the type; nothing existing changes.

alter type public.revenueos_doc_type add value if not exists 'company_profile';
alter type public.revenueos_doc_type add value if not exists 'product_catalogue';
alter type public.revenueos_doc_type add value if not exists 'brochure';
alter type public.revenueos_doc_type add value if not exists 'presentation';
alter type public.revenueos_doc_type add value if not exists 'case_study';
alter type public.revenueos_doc_type add value if not exists 'email_templates';
