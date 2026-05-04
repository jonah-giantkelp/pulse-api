from supabase import create_client, Client
from pulse_api.config import settings

supabase: Client = create_client(settings.supabase_url, settings.supabase_key)
