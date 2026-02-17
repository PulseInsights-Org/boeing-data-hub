create table public.batches (
  id character varying(36) not null,
  batch_type character varying(20) not null,
  status character varying(20) null default 'pending'::character varying,
  total_items integer not null default 0,
  extracted_count integer null default 0,
  normalized_count integer null default 0,
  published_count integer null default 0,
  failed_count integer null default 0,
  error_message text null,
  failed_items jsonb null default '[]'::jsonb,
  celery_task_id character varying(100) null,
  idempotency_key character varying(100) null,
  created_at timestamp with time zone null default now(),
  updated_at timestamp with time zone null default now(),
  completed_at timestamp with time zone null,
  user_id character varying(50) not null default 'system'::character varying,
  part_numbers text[] null default '{}'::text[],
  publish_part_numbers text[] null,
  failed_part_numbers text[] null default '{}'::text[],
  constraint batches_pkey primary key (id),
  constraint batches_idempotency_key_key unique (idempotency_key),
  constraint batches_batch_type_check check (
    (
      (batch_type)::text = any (
        (
          array[
            'search'::character varying,
            'normalized'::character varying,
            'publishing'::character varying,
            'publish'::character varying
          ]
        )::text[]
      )
    )
  ),
  constraint batches_status_check check (
    (
      (status)::text = any (
        array[
          'pending'::text,
          'processing'::text,
          'completed'::text,
          'failed'::text,
          'cancelled'::text
        ]
      )
    )
  )
) TABLESPACE pg_default;

create index IF not exists idx_batches_status on public.batches using btree (status, created_at desc) TABLESPACE pg_default;

create index IF not exists idx_batches_idempotency on public.batches using btree (idempotency_key) TABLESPACE pg_default
where
  (idempotency_key is not null);

create index IF not exists idx_batches_active on public.batches using btree (created_at desc) TABLESPACE pg_default
where
  (
    (status)::text = any (array['pending'::text, 'processing'::text])
  );

create index IF not exists idx_batches_user_id on public.batches using btree (user_id) TABLESPACE pg_default;

create index IF not exists idx_batches_idempotency_key on public.batches using btree (idempotency_key) TABLESPACE pg_default;

create table public.boeing_raw_data (
  id uuid not null default gen_random_uuid (),
  created_at timestamp with time zone not null default now(),
  search_query text not null,
  raw_payload jsonb not null,
  user_id text not null default 'system'::text,
  constraint boeing_raw_data_pkey primary key (id)
) TABLESPACE pg_default;

create index IF not exists idx_boeing_raw_data_user_id on public.boeing_raw_data using btree (user_id) TABLESPACE pg_default;

create table public.product (
  id text not null,
  sku text not null,
  title text not null,
  body_html text null,
  vendor text null,
  price numeric null,
  currency text null,
  inventory_quantity integer null,
  inventory_status text null,
  weight numeric null,
  weight_unit text null,
  country_of_origin text null,
  dim_length numeric null,
  dim_width numeric null,
  dim_height numeric null,
  dim_uom text null,
  shopify_product_id text null,
  shopify_variant_id text null,
  shopify_handle text null,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now(),
  image_url text null,
  image_path text null,
  boeing_image_url text null,
  boeing_thumbnail_url text null,
  base_uom text null,
  hazmat_code text null,
  faa_approval_code text null,
  eccn text null,
  schedule_b_code text null,
  supplier_name text null,
  boeing_name text null,
  boeing_description text null,
  list_price numeric null,
  net_price numeric null,
  cost_per_item numeric null,
  location_summary text null,
  condition text null,
  pma boolean null,
  estimated_lead_time_days integer null,
  trace text null,
  expiration_date date null,
  notes text null,
  user_id text not null default 'system'::text,
  constraint product_pkey primary key (id),
  constraint product_user_sku_unique unique (user_id, sku)
) TABLESPACE pg_default;

create index IF not exists idx_product_user_id on public.product using btree (user_id) TABLESPACE pg_default;

create trigger trg_product_updated_at BEFORE
update on product for EACH row
execute FUNCTION set_product_updated_at ();

create table public.product_staging (
  id text not null,
  sku text not null,
  title text not null,
  body_html text null,
  vendor text null,
  price numeric null,
  currency text null,
  inventory_quantity integer null,
  inventory_status text null,
  weight numeric null,
  weight_unit text null,
  country_of_origin text null,
  dim_length numeric null,
  dim_width numeric null,
  dim_height numeric null,
  dim_uom text null,
  status text not null default 'fetched'::text,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now(),
  image_url text null,
  image_path text null,
  boeing_image_url text null,
  boeing_thumbnail_url text null,
  base_uom text null,
  hazmat_code text null,
  faa_approval_code text null,
  eccn text null,
  schedule_b_code text null,
  supplier_name text null,
  boeing_name text null,
  boeing_description text null,
  list_price numeric null,
  net_price numeric null,
  cost_per_item numeric null,
  location_summary text null,
  condition text null,
  pma boolean null,
  estimated_lead_time_days integer null,
  trace text null,
  expiration_date date null,
  notes text null,
  user_id text not null default 'system'::text,
  shopify_product_id text null,
  batch_id text null,
  constraint product_staging_pkey primary key (id),
  constraint product_staging_user_sku_unique unique (user_id, sku)
) TABLESPACE pg_default;

create index IF not exists idx_product_staging_user_id on public.product_staging using btree (user_id) TABLESPACE pg_default;

create index IF not exists idx_product_staging_shopify_id on public.product_staging using btree (shopify_product_id) TABLESPACE pg_default
where
  (shopify_product_id is not null);

create index IF not exists idx_product_staging_batch_id on public.product_staging using btree (batch_id) TABLESPACE pg_default;

create trigger trg_product_staging_updated_at BEFORE
update on product_staging for EACH row
execute FUNCTION set_product_staging_updated_at ();

create trigger trg_update_batch_stats
after INSERT
or DELETE
or
update on product_staging for EACH row
execute FUNCTION update_batch_stats_on_product_change ();

create table public.product_sync_schedule (
  id uuid not null default gen_random_uuid (),
  user_id text not null,
  sku text not null,
  hour_bucket smallint not null,
  sync_status text not null default 'pending'::text,
  last_sync_at timestamp with time zone null,
  consecutive_failures integer not null default 0,
  last_error text null,
  last_boeing_hash text null,
  last_price numeric null,
  last_quantity integer null,
  last_inventory_status text null,
  last_locations jsonb null,
  is_active boolean not null default true,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now(),
  constraint product_sync_schedule_pkey primary key (id),
  constraint product_sync_schedule_user_sku_unique unique (user_id, sku),
  constraint product_sync_schedule_hour_bucket_check check (
    (
      (hour_bucket >= 0)
      and (hour_bucket <= 23)
    )
  ),
  constraint product_sync_schedule_sync_status_check check (
    (
      sync_status = any (
        array[
          'pending'::text,
          'syncing'::text,
          'success'::text,
          'failed'::text
        ]
      )
    )
  )
) TABLESPACE pg_default;

create index IF not exists idx_sync_hourly_dispatch on public.product_sync_schedule using btree (hour_bucket, sync_status, last_sync_at) TABLESPACE pg_default
where
  (is_active = true);

create index IF not exists idx_sync_slot_distribution on public.product_sync_schedule using btree (hour_bucket) TABLESPACE pg_default
where
  (is_active = true);

create index IF not exists idx_sync_failed_products on public.product_sync_schedule using btree (consecutive_failures, last_sync_at) TABLESPACE pg_default
where
  (
    (is_active = true)
    and (sync_status = 'failed'::text)
  );

create index IF not exists idx_sync_stuck on public.product_sync_schedule using btree (last_sync_at) TABLESPACE pg_default
where
  (sync_status = 'syncing'::text);

create index IF not exists idx_sync_user on public.product_sync_schedule using btree (user_id, is_active) TABLESPACE pg_default;

create trigger trg_sync_schedule_updated_at BEFORE
update on product_sync_schedule for EACH row
execute FUNCTION set_updated_at ();