#!/bin/sh
# ═══════════════════════════════════════════════════════════════════════════════
# Initialisation Garage pour Alambic.
# Crée : la layout single-node, les 3 buckets (input/work/storage), et une clé
# applicative dédiée avec accès limité à ces buckets (pas la clé root).
# Idempotent : relançable sans casse (les "already exists" sont tolérés).
#
# Variables attendues :
#   GARAGE_RPC_HOST       hôte du conteneur garage (défaut: garage)
#   ALAMBIC_S3_KEY_NAME   nom de la clé applicative (défaut: alambic-app)
#   MINIO_BUCKET_*        noms des 3 buckets (réutilise les vars du .env)
# ═══════════════════════════════════════════════════════════════════════════════
set -eu

# Binaire à la racine de l'image (/garage), avec le chemin de config explicite.
# Sans -c, le binaire ne trouve pas garage.toml et échoue.
GARAGE="/garage -c /etc/garage.toml"
B_INPUT="${MINIO_BUCKET_INPUT:-alambic-input}"
B_WORK="${MINIO_BUCKET_WORK:-alambic-work}"
B_STORAGE="${MINIO_BUCKET_STORAGE:-alambic-storage}"
KEY_NAME="${ALAMBIC_S3_KEY_NAME:-alambic-app}"

echo "→ Attente que Garage réponde…"
until $GARAGE status >/dev/null 2>&1; do sleep 1; done
echo "  Garage est prêt."

# ── 1. Layout single-node ────────────────────────────────────────────────────
# Récupère l'ID du nœud local et lui assigne une capacité, si pas déjà fait.
NODE_ID=$($GARAGE status | awk 'NR>2 && $1 != "" {print $1; exit}')
if $GARAGE layout show 2>/dev/null | grep -q "$NODE_ID"; then
  echo "→ Layout déjà configurée."
else
  echo "→ Configuration de la layout (nœud $NODE_ID, zone fr, 1G)…"
  $GARAGE layout assign -z fr -c 1G "$NODE_ID"
  $GARAGE layout apply --version 1
fi

# ── 2. Buckets ───────────────────────────────────────────────────────────────
for b in "$B_INPUT" "$B_WORK" "$B_STORAGE"; do
  if $GARAGE bucket info "$b" >/dev/null 2>&1; then
    echo "→ Bucket $b existe déjà."
  else
    echo "→ Création du bucket $b…"
    $GARAGE bucket create "$b"
  fi
done

# ── 3. Clé applicative dédiée (accès limité aux 3 buckets) ───────────────────
if $GARAGE key info "$KEY_NAME" >/dev/null 2>&1; then
  echo "→ Clé $KEY_NAME existe déjà."
else
  echo "→ Création de la clé applicative $KEY_NAME…"
  $GARAGE key create "$KEY_NAME"
fi

echo "→ Attribution des droits read/write sur les 3 buckets…"
for b in "$B_INPUT" "$B_WORK" "$B_STORAGE"; do
  $GARAGE bucket allow --read --write "$b" --key "$KEY_NAME"
done

echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "  Garage initialisé. Récupère les identifiants de la clé applicative :"
echo "    docker compose exec garage garage key info $KEY_NAME --show-secret"
echo "  Puis renseigne ALAMBIC_S3_ACCESS_KEY / ALAMBIC_S3_SECRET_KEY dans .env"
echo "════════════════════════════════════════════════════════════════════"