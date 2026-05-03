# Pseudocode shape
def main():
    args = parse_args()                          # --pico-port, --traces N=1000, --out report.json
    pod = collect.pod.open_pod(args.pico_port)
    traces_a, traces_b = collect.traces.collect_two_groups(pod, n=args.traces)

    findings = []
    findings += analyze.ct_lint.scan(args.source_cpp)
    findings += analyze.tvla.scan(traces_a, traces_b)
    findings += analyze.cpa.scan(traces_a, traces_b, plaintexts)
    findings += ml.classifier.scan(traces_a + traces_b)

    report = pipeline.findings.merge(findings, target=args.source_cpp)
    print(json.dumps(report.to_dict()))