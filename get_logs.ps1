$gcloud = 'C:\Users\2096955\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd'
$filter = 'resource.labels.service_name="medliaison"'
& $gcloud logging read $filter --limit=30 --format="table(timestamp,severity,textPayload)" --freshness=3m --project=gbg-neuro 2>&1
