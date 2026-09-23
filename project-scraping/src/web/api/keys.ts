// Query-key factory for the web app's frozen endpoints.
export const keys = {
  sources: () => ['sources'],
  records: (sourceId: string, offset: number, limit: number) =>
    ['records', sourceId || '', offset, limit],
  recordDetail: (recordId: number | null) => ['record-detail', recordId],
}
