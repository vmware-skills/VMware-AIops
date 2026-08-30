"""What every skill in the family exposes — generated, do not edit.

Written by scripts/install_capability_evals.sh, which is the only place
that can see all twelve repos at once. Error messages and tool
descriptions route across skills, so checking those citations needs the
sibling surfaces; deriving them here beats a hand-kept list that goes
stale without saying so.
"""

from __future__ import annotations

FAMILY_TOOLS: dict[str, frozenset[str]] = {
    'vmware-aiops': frozenset({
        'acknowledge_vcenter_alarm', 'add_host_vmk', 'attach_iso_to_vm', 'batch_clone_vms',
        'batch_deploy_from_spec', 'batch_linked_clone_vms', 'browse_datastore',
        'cluster_add_host', 'cluster_configure', 'cluster_create', 'cluster_delete',
        'cluster_health_summary', 'cluster_info', 'cluster_remove_host',
        'convert_vm_to_template', 'create_drs_rule', 'create_dvs_portgroup',
        'cross_vcenter_attention', 'datastore_investigation_bundle', 'delete_drs_rule',
        'deploy_linked_clone', 'deploy_vm_from_ova', 'deploy_vm_from_template',
        'host_investigation_bundle', 'list_drs_rules', 'list_dvs_portgroups',
        'list_host_vmks', 'list_vcenter_alarms', 'remove_host_vmk', 'reset_vcenter_alarm',
        'scan_datastore_images', 'set_drs_rule_enabled', 'set_vmk_service', 'vm_apply_plan',
        'vm_cancel_ttl', 'vm_clean_slate', 'vm_clone', 'vm_create', 'vm_create_plan',
        'vm_create_snapshot', 'vm_delete', 'vm_delete_snapshot', 'vm_guest_download',
        'vm_guest_exec', 'vm_guest_exec_output', 'vm_guest_provision', 'vm_guest_upload',
        'vm_investigation_bundle', 'vm_list_plans', 'vm_list_snapshots', 'vm_list_ttl',
        'vm_migrate', 'vm_power_off', 'vm_power_on', 'vm_reconfigure', 'vm_revert_snapshot',
        'vm_rollback_plan', 'vm_set_ttl', 'vm_task_status', 'vmk_ping',
    }),
    'vmware-aria': frozenset({
        'acknowledge_alert', 'cancel_alert', 'create_alert_definition',
        'delete_alert_definition', 'delete_report', 'findings_list',
        'fleet_certificate_list', 'fleet_domain_list', 'fleet_password_account_list',
        'generate_report', 'get_alert', 'get_aria_health', 'get_capacity_overview',
        'get_remaining_capacity', 'get_report', 'get_resource', 'get_resource_health',
        'get_resource_metrics', 'get_resource_riskbadge', 'get_time_remaining',
        'get_top_consumers', 'investigate_alert', 'list_alert_definitions', 'list_alerts',
        'list_anomalies', 'list_collector_groups', 'list_report_definitions',
        'list_reports', 'list_resources', 'list_rightsizing_recommendations',
        'list_symptom_definitions', 'promql_query', 'set_alert_definition_state',
    }),
    'vmware-avi': frozenset({
        'ako_amko_status', 'ako_clusters', 'ako_config_diff', 'ako_config_show',
        'ako_config_upgrade', 'ako_ingress_check', 'ako_ingress_diagnose',
        'ako_ingress_map', 'ako_logs', 'ako_restart', 'ako_status', 'ako_sync_diff',
        'ako_sync_force', 'ako_sync_status', 'ako_version', 'pool_list',
        'pool_member_disable', 'pool_member_enable', 'pool_members', 'se_health', 'se_list',
        'ssl_expiry_check', 'ssl_list', 'vs_analytics', 'vs_error_logs', 'vs_list',
        'vs_status', 'vs_toggle',
    }),
    'vmware-debug': frozenset({
        'case_close', 'case_get', 'case_grade', 'case_hypotheses', 'case_knowledge',
        'case_list', 'case_open', 'case_plan', 'case_readiness', 'case_record_gap',
        'case_submit_evidence', 'case_timeline', 'incident_timeline',
        'list_symptom_categories',
    }),
    'vmware-harden': frozenset({
        'describe_stig_content_sync', 'get_baseline_rules', 'get_remediation',
        'list_baselines', 'list_drift_events', 'list_stig_controls', 'list_violations',
        'scan_target',
    }),
    'vmware-log-insight': frozenset({
        'alert_get', 'alert_history', 'alert_list', 'log_aggregate', 'log_fields',
        'log_search', 'log_version',
    }),
    'vmware-monitor': frozenset({
        'active_sessions', 'active_tasks', 'certificate_status', 'cluster_health_summary',
        'cluster_last_apply_result', 'cluster_patch_compliance', 'cross_vcenter_attention',
        'datastore_capacity', 'datastore_investigation_bundle', 'get_alarms', 'get_events',
        'get_host_sensors', 'get_host_services', 'host_investigation_bundle',
        'host_log_scan', 'host_memory_tiering', 'host_performance', 'license_status',
        'list_all_clusters', 'list_all_datastores', 'list_all_networks', 'list_esxi_hosts',
        'list_virtual_machines', 'ntp_status', 'resource_pool_usage', 'snapshot_aging',
        'vcenter_deployment_size', 'vm_info', 'vm_investigation_bundle',
        'vm_list_snapshots', 'vm_performance',
    }),
    'vmware-nsx': frozenset({
        'configure_tier0_bgp', 'create_ip_pool', 'create_nat_rule', 'create_segment',
        'create_static_route', 'create_tier1_gateway', 'delete_ip_pool', 'delete_nat_rule',
        'delete_segment', 'delete_static_route', 'delete_tier1_gateway',
        'get_bgp_neighbors', 'get_edge_cluster_status', 'get_ip_pool_usage',
        'get_logical_port_status', 'get_nsx_manager_status', 'get_segment',
        'get_segment_port_for_vm', 'get_tier0_gateway', 'get_tier1_gateway',
        'get_transport_node_status', 'list_edge_clusters', 'list_ip_pools',
        'list_nat_rules', 'list_nsx_alarms', 'list_segments', 'list_static_routes',
        'list_tier0_gateways', 'list_tier1_gateways', 'list_transport_nodes',
        'list_transport_zones', 'update_segment', 'update_tier1_gateway',
    }),
    'vmware-nsx-security': frozenset({
        'apply_vm_tag', 'create_dfw_policy', 'create_dfw_rule', 'create_group',
        'delete_dfw_policy', 'delete_dfw_rule', 'delete_group', 'get_dfw_policy',
        'get_dfw_rule_stats', 'get_group', 'get_idps_status', 'get_traceflow_result',
        'list_dfw_exclusions', 'list_dfw_policies', 'list_dfw_rules', 'list_groups',
        'list_idps_profiles', 'list_vm_tags', 'remove_vm_tag', 'run_traceflow',
        'update_dfw_policy', 'update_dfw_rule',
    }),
    'vmware-pilot': frozenset({
        'approve', 'cancel_workflow', 'confirm_draft', 'create_workflow', 'design_workflow',
        'get_skill_catalog', 'get_workflow_status', 'list_workflows', 'plan_workflow',
        'review_workflow', 'rollback', 'run_workflow', 'update_draft',
    }),
    'vmware-storage': frozenset({
        'browse_datastore', 'list_all_datastores', 'list_cached_images',
        'scan_datastore_images', 'storage_iscsi_add_target', 'storage_iscsi_enable',
        'storage_iscsi_remove_target', 'storage_iscsi_status', 'storage_rescan',
        'vsan_capacity', 'vsan_efficiency', 'vsan_health',
    }),
    'vmware-vks': frozenset({
        'check_vks_compatibility', 'create_namespace', 'create_tkc_cluster',
        'delete_namespace', 'delete_tkc_cluster', 'get_harbor_info', 'get_namespace',
        'get_supervisor_kubeconfig', 'get_supervisor_status', 'get_tkc_available_versions',
        'get_tkc_cluster', 'get_tkc_kubeconfig', 'list_namespace_storage_usage',
        'list_namespaces', 'list_supervisor_storage_policies', 'list_tkc_clusters',
        'list_vm_classes', 'list_vm_groups', 'list_vm_network_interfaces',
        'list_vm_snapshots', 'scale_tkc_cluster', 'update_namespace', 'upgrade_tkc_cluster',
    }),
}

FAMILY_COMMANDS: dict[str, frozenset[str]] = {
    'vmware-aiops': frozenset({
        'alarm acknowledge', 'alarm list', 'alarm reset', 'attention', 'cluster add-host',
        'cluster configure', 'cluster create', 'cluster delete', 'cluster drs-rule-create',
        'cluster drs-rule-delete', 'cluster drs-rule-set', 'cluster drs-rules',
        'cluster info', 'cluster remove-host', 'daemon start', 'daemon status',
        'daemon stop', 'datastore browse', 'datastore scan-images', 'deploy batch',
        'deploy batch-clone', 'deploy iso', 'deploy linked-clone', 'deploy mark-template',
        'deploy ova', 'deploy template', 'doctor', 'hub status', 'init',
        'investigate datastore', 'investigate host', 'investigate vm', 'mcp',
        'mcp-config generate', 'mcp-config install', 'mcp-config list', 'plan list',
        'scan now', 'summary', 'vm cancel-ttl', 'vm clean-slate', 'vm clone', 'vm create',
        'vm delete', 'vm guest-download', 'vm guest-exec', 'vm guest-upload', 'vm list-ttl',
        'vm migrate', 'vm power-off', 'vm power-on', 'vm reconfigure', 'vm set-ttl',
        'vm snapshot-create', 'vm snapshot-delete', 'vm snapshot-list',
        'vm snapshot-revert', 'vm task-status',
    }),
    'vmware-aria': frozenset({
        'alert acknowledge', 'alert cancel', 'alert definitions', 'alert get', 'alert list',
        'anomaly list', 'anomaly risk', 'capacity overview', 'capacity remaining',
        'capacity rightsizing', 'capacity time-remaining', 'doctor', 'fleet certificates',
        'fleet domains', 'fleet findings', 'fleet passwords', 'fleet promql',
        'health collectors', 'health status', 'init', 'mcp', 'report definitions',
        'report delete', 'report generate', 'report get', 'report list', 'resource get',
        'resource health', 'resource list', 'resource metrics', 'resource top',
    }),
    'vmware-avi': frozenset({
        'ako amko-status', 'ako clusters', 'ako config-diff', 'ako config-show',
        'ako config-upgrade', 'ako ingress-check', 'ako ingress-diagnose',
        'ako ingress-map', 'ako logs', 'ako restart', 'ako status', 'ako sync-diff',
        'ako sync-force', 'ako sync-status', 'ako version', 'analytics', 'config', 'doctor',
        'init', 'logs', 'mcp', 'pool disable', 'pool enable', 'pool members', 'se health',
        'se list', 'ssl expiry', 'ssl list', 'vs disable', 'vs enable', 'vs list',
        'vs status',
    }),
    'vmware-debug': frozenset({
        'categories', 'mcp', 'triage', 'version',
    }),
    'vmware-harden': frozenset({
        'advise', 'apply', 'baseline import', 'baseline list', 'baseline validate',
        'doctor', 'drift', 'mcp', 'report', 'scan', 'stig controls', 'stig sync-info',
        'web',
    }),
    'vmware-log-insight': frozenset({
        'aggregate', 'alert get', 'alert history', 'alert list', 'doctor', 'fields', 'mcp',
        'search', 'version',
    }),
    'vmware-monitor': frozenset({
        'activity sessions', 'activity tasks', 'attention', 'capacity datastores',
        'capacity pools', 'daemon start', 'daemon status', 'daemon stop', 'deployment-size',
        'doctor', 'health alarms', 'health events', 'health sensors', 'health services',
        'infra certs', 'infra licenses', 'infra ntp', 'init', 'inventory clusters',
        'inventory datastores', 'inventory hosts', 'inventory networks', 'inventory vms',
        'investigate datastore', 'investigate host', 'investigate vm', 'mcp',
        'mcp-config generate', 'mcp-config list', 'memory tiering', 'patch compliance',
        'patch last-apply', 'perf hosts', 'perf vms', 'scan now', 'snapshots aging',
        'summary', 'vm info', 'vm snapshot-list',
    }),
    'vmware-nsx': frozenset({
        'doctor', 'gateway configure-tier0-bgp', 'gateway create-tier1',
        'gateway delete-tier1', 'gateway update-tier1', 'health alarms',
        'health edge-cluster-status', 'health manager-status',
        'health transport-node-status', 'init', 'inventory get-segment',
        'inventory get-tier0', 'inventory get-tier1', 'inventory list-edge-clusters',
        'inventory list-segments', 'inventory list-tier0s', 'inventory list-tier1s',
        'inventory list-transport-nodes', 'inventory list-transport-zones',
        'ip-pool create', 'ip-pool delete', 'mcp', 'mcp-config generate',
        'mcp-config install', 'mcp-config list', 'nat create-rule', 'nat delete-rule',
        'networking bgp-neighbors', 'networking ip-pool-usage', 'networking list-ip-pools',
        'networking list-nat-rules', 'networking list-static-routes', 'route create-static',
        'route delete-static', 'segment create', 'segment delete', 'segment update',
        'troubleshoot port-status', 'troubleshoot vm-segment',
    }),
    'vmware-nsx-security': frozenset({
        'doctor', 'group delete', 'group get', 'group list', 'idps profiles', 'idps status',
        'init', 'mcp', 'policy create', 'policy delete', 'policy get', 'policy list',
        'rule delete', 'rule list', 'rule stats', 'tag apply', 'tag list', 'tag remove',
        'traceflow run',
    }),
    'vmware-pilot': frozenset({
        'mcp', 'version',
    }),
    'vmware-storage': frozenset({
        'datastore browse', 'datastore list', 'datastore scan-images', 'doctor', 'init',
        'iscsi add-target', 'iscsi enable', 'iscsi remove-target', 'iscsi rescan',
        'iscsi status', 'mcp', 'vsan capacity', 'vsan efficiency', 'vsan health',
    }),
    'vmware-vks': frozenset({
        'check', 'harbor', 'init', 'kubeconfig get', 'kubeconfig supervisor', 'mcp',
        'namespace create', 'namespace delete', 'namespace get', 'namespace list',
        'namespace update', 'namespace vm-classes', 'preflight-auth', 'storage',
        'supervisor status', 'supervisor storage-policies', 'tkc create', 'tkc delete',
        'tkc get', 'tkc list', 'tkc scale', 'tkc upgrade', 'tkc versions', 'vm groups',
        'vm nics', 'vm snapshots',
    }),
}

#: Every tool name anywhere in the family, for citation checks that do
#: not care which skill owns the name.
ALL_TOOLS: frozenset[str] = frozenset().union(*FAMILY_TOOLS.values())
