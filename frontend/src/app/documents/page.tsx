import { DocumentsTable } from "@/components/DocumentsTable";
import { Uploader } from "@/components/Uploader";

export default function DocumentsPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Documents</h1>
      <Uploader />
      <DocumentsTable />
    </div>
  );
}
