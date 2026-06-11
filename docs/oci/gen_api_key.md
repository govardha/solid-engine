Phase 1: Generate OCI API Key
mkdir -p ~/.oci

# Generate 2048-bit RSA key (OCI requirement — won't accept smaller)

openssl genrsa -out ~/.oci/oci_api_key.pem 2048
chmod 600 ~/.oci/oci_api_key.pem

# Extract public key

openssl rsa -pubout -in ~/.oci/oci_api_key.pem -out ~/.oci/oci_api_key_public.pem

Phase 2: Upload Public Key to OCI Console

Login → top-right Profile icon → My Profile (or User Settings)
Left panel → API Keys → Add API Key
Select Paste Public Key → paste contents of ~/.oci/oci_api_key_public.pem
OCI will show you a config preview — copy it. Looks like:

[DEFAULT]
user=ocid1.user.oc1..aaaaaaaXXXXXXXXXX
fingerprint=aa:bb:cc:dd:ee:ff:...
tenancy=ocid1.tenancy.oc1..aaaaaaaXXXXXXXXXX
region=us-ashburn-1
key_file=~/.oci/oci_api_key.pem

Phase 3: Write the Config File
cat > ~/.oci/config << 'EOF'
[DEFAULT]
user=ocid1.user.oc1..YOUR_USER_OCID
fingerprint=YOUR:FINGERPRINT:HERE
tenancy=ocid1.tenancy.oc1..YOUR_TENANCY_OCID
region=us-ashburn-1
key_file=/home/YOUR_USER/.oci/oci_api_key.pem
EOF

Phase 4: Update the key_file location from the previous line to ~/.oci/oci_api_key.pem
chmod 600 ~/.oci/config

# For MacOS and in Windows Install Ubuntu via WSL

brew install oci-cli

Phase 5: Now the API key is in, test it with the following command.

oci iam region list --output table

# If this returns regions, you're authenticated.
