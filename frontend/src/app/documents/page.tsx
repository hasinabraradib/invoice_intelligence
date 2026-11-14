import { DocumentsTable } from "@/components/DocumentsTable";
import { Uploader } from "@/components/Uploader";
import { PageHeader } from "@/components/ui";

export default function DocumentsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Library"
        title="Documents"
        description="Upload invoices, follow their processing, and open any one to review its fields with evidence."
      />
      <Uploader />
      <DocumentsTable />
    </div>
  );
}
