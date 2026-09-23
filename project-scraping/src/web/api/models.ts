// Response shapes for the web app's endpoints. One interface per payload;
// queries import these instead of redeclaring fields.
export interface Source {
  id: string
  name: string
  url: string | null
  kind: string | null
  expected_count: number | null
  record_count: number
}

export interface RecordItem {
  id: number
  source_id: string
  source_key: string | null
  url: string | null
  name: string | null
  scraped_at: string
  published_at: string
}

export interface FeatureUnit {
  id: number
  name: string
  sanitized_name: string | null
}

export interface RecordFeature {
  id: number
  record_id: number
  name: string
  value: string
  sanitized_name: string | null
  unit_id: number | null
  unit: FeatureUnit | null
}

export interface RecordDetail extends RecordItem {
  features: RecordFeature[]
}

export interface RecordPage {
  items: RecordItem[]
  total: number
  has_more: boolean
}
