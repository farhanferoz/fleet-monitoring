Where Prometheus sends alerts. With the "alerting" role on the same machine, copy
`local.yml.example` to `local.yml`. For an Alertmanager on another machine, put its
`host:port` there instead. With no `.yml` file here, Prometheus sends alerts nowhere
(they still show on its Alerts page).
