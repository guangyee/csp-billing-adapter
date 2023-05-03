#
# Copyright 2023 SUSE LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

#
# Unit tests for the csp_billing_adapter.bill_utils
#

import datetime

from unittest import mock

from pytest import (
    mark,
    raises
)

from csp_billing_adapter.bill_utils import (
    get_average_usage,
    get_billable_usage,
    get_billing_dimensions,
    get_max_usage,
    get_volume_dimensions,
    process_metering
)
from csp_billing_adapter.config import Config
from csp_billing_adapter.csp_cache import (
    add_usage_record,
    create_cache
)
from csp_billing_adapter.csp_config import create_csp_config
from csp_billing_adapter.exceptions import (
    NoMatchingVolumeDimensionError
)
from csp_billing_adapter.utils import (
    date_to_string,
    string_to_date,
    get_now,
    get_date_delta,
    get_next_bill_time,
    get_prev_bill_time
)


# helper routines
def gen_metric_usage_records(
    now: datetime.datetime,
    config: Config,
    metric: str,
    first_value: int,
    increment: int,
    count: int
) -> list:
    return [
        {
            "reporting_time": date_to_string(
                get_date_delta(
                    now,
                    -(config.reporting_interval * 3)
                )
            ),
            metric: first_value
        },
        {
            "reporting_time": date_to_string(
                get_date_delta(
                    now,
                    -(config.reporting_interval * 2)
                )
            ),
            metric: first_value + (1 * increment)
        },
        {
            "reporting_time": date_to_string(
                get_date_delta(
                    now,
                    -(config.reporting_interval * 1)
                )
            ),
            metric: first_value + (2 * increment)
        }
    ]


def gen_mixed_usage_records(
    bill_time: datetime.datetime,
    config: Config,
    billing_period_only: bool = True
) -> list:
    test_usage_records = [
        {
            "reporting_time": date_to_string(
                get_prev_bill_time(
                    get_prev_bill_time(
                        bill_time,
                        config.billing_interval
                    ),
                    config.billing_interval
                )
            ),
            "jobs": 44,
            "nodes": 9
        },
        {
            "reporting_time": date_to_string(
                get_date_delta(
                    bill_time,
                    -(config.reporting_interval * 3)
                )
            ),
            "jobs": 15,
            "nodes": 4
        },
        {
            "reporting_time": date_to_string(
                get_date_delta(
                    bill_time,
                    -(config.reporting_interval * 2)
                )
            ),
            "jobs": 23,
            "nodes": 6
        },
        {
            "reporting_time": date_to_string(
                get_date_delta(
                    bill_time,
                    -(config.reporting_interval * 1)
                )
            ),
            "jobs": 28,
            "nodes": 7
        },
        {
            "reporting_time": date_to_string(
                get_next_bill_time(
                    bill_time,
                    config.billing_interval
                ),
            ),
            "jobs": 63,
            "nodes": 15
        }
    ]

    if billing_period_only:
        usage_records = test_usage_records[1:-1]
    else:
        usage_records = test_usage_records

    return usage_records


# test routines
def test_get_average_usage():
    metric1 = "dim1"
    usage_records1 = [
        {metric1: 1},
        {metric1: 1},
        {metric1: 1},
    ]
    metric2 = "dim2"
    usage_records2 = [
        {metric2: 1},
        {metric2: 2},
        {metric2: 3},
    ]

    average_usage = get_average_usage(metric1, [])
    assert average_usage == 0

    average_usage = get_average_usage(metric1, usage_records1)
    assert average_usage == 1

    average_usage = get_average_usage(metric2, usage_records2)
    assert average_usage == 2


def test_get_max_usage():
    metric1 = "dim1"
    usage_records1 = [
        {metric1: 1},
        {metric1: 1},
        {metric1: 1},
    ]
    metric2 = "dim2"
    usage_records2 = [
        {metric2: 1},
        {metric2: 2},
        {metric2: 3},
    ]

    max_usage = get_max_usage(metric1, [])
    assert max_usage == 0

    max_usage = get_max_usage(metric1, usage_records1)
    assert max_usage == 1

    max_usage = get_max_usage(metric2, usage_records2)
    assert max_usage == 3


def test_get_billage_usage_empty(cba_config):
    metric = "managed_node_count"

    billable_usage = get_billable_usage(
        usage_records=[],
        config=cba_config,
        empty_usage=True
    )

    assert metric in billable_usage
    assert billable_usage[metric] == 0


def test_get_billable_usage_average(cba_config):
    now = get_now()
    metric = "managed_node_count"
    usage_records1 = gen_metric_usage_records(
        now,
        cba_config,
        metric,
        first_value=1,
        increment=0,
        count=3
    )
    usage_records2 = gen_metric_usage_records(
        now,
        cba_config,
        metric,
        first_value=1,
        increment=1,
        count=3
    )

    # verify correct average is calculated for constant usage
    billable_usage = get_billable_usage(
        usage_records=usage_records1,
        config=cba_config,
        empty_usage=False
    )

    assert metric in billable_usage
    assert billable_usage[metric] == 1  # average of [1, 1, 1]

    # verify correct average is calculated for variable usage
    billable_usage = get_billable_usage(
        usage_records=usage_records2,
        config=cba_config,
        empty_usage=False
    )

    assert metric in billable_usage
    assert billable_usage[metric] == 2  # average of [1, 2, 3]


@mark.config('config_good_maximum.yaml')
def test_get_billable_usage_maximum(cba_config):
    now = get_now()
    metric = "managed_node_count"
    usage_records1 = gen_metric_usage_records(
        now,
        cba_config,
        metric,
        first_value=1,
        increment=0,
        count=3
    )
    usage_records2 = gen_metric_usage_records(
        now,
        cba_config,
        metric,
        first_value=1,
        increment=1,
        count=3
    )

    # verify correct maximum is calculated for constant usage
    billable_usage = get_billable_usage(
        usage_records=usage_records1,
        config=cba_config,
        empty_usage=False
    )

    assert metric in billable_usage
    assert billable_usage[metric] == 1  # max of [1, 1, 1]

    # verify correct maximum is calculated for variable usage
    billable_usage = get_billable_usage(
        usage_records=usage_records2,
        config=cba_config,
        empty_usage=False
    )

    assert metric in billable_usage
    assert billable_usage[metric] == 3  # max of [1, 2, 3]


@mark.config('config_testing_mixed.yaml')
def test_get_volume_dimensions(cba_config):
    test_billable_usage = {
        "jobs": 72,
        "nodes": 7
    }
    test_tiers = {
        "jobs": "jobs_tier_3",
        "nodes": "nodes_tier_2"
    }

    for metric, usage in test_billable_usage.items():
        metric_dimensions = cba_config.usage_metrics[metric]['dimensions']
        billed_dimensions = {}

        get_volume_dimensions(
            usage_metric=metric,
            usage=usage,
            metric_dimensions=metric_dimensions,
            billed_dimensions=billed_dimensions
        )

        assert test_tiers[metric] in billed_dimensions
        assert billed_dimensions[test_tiers[metric]] == usage


@mark.config('config_broken_dimensions.yaml')
def test_get_volume_dimensions_invalid(cba_config):
    test_billable_usage = {
        "managed_node_count": 501
    }

    for metric, usage in test_billable_usage.items():
        metric_dimensions = cba_config.usage_metrics[metric]['dimensions']
        billed_dimensions = {}

        with raises(NoMatchingVolumeDimensionError) as e:
            get_volume_dimensions(
                usage_metric=metric,
                usage=usage,
                metric_dimensions=metric_dimensions,
                billed_dimensions=billed_dimensions
            )

        assert e.value.metric == metric
        assert e.value.value == usage


@mark.config('config_testing_mixed.yaml')
def test_get_billing_dimensions(cba_config):
    test_billable_usage = {
        "jobs": 72,
        "nodes": 7
    }
    test_tiers = {
        "jobs": "jobs_tier_3",
        "nodes": "nodes_tier_2"
    }
    test_billed_dimensions = {
        test_tiers["jobs"]: test_billable_usage["jobs"],
        test_tiers["nodes"]: test_billable_usage["nodes"]
    }

    billed_dimensions = get_billing_dimensions(
        config=cba_config,
        billable_usage=test_billable_usage
    )

    assert billed_dimensions == test_billed_dimensions


@mark.config('config_testing_mixed.yaml')
def test_process_metering(cba_pm, cba_config):
    # initialise the cache
    create_cache(
        hook=cba_pm.hook,
        config=cba_config
    )

    test_cache = cba_pm.hook.get_cache(config=cba_config)
    assert 'adapter_start_time' in test_cache
    assert 'next_bill_time' in test_cache
    assert 'next_reporting_time' in test_cache
    assert 'usage_records' in test_cache
    assert test_cache["usage_records"] == []
    assert 'last_bill' in test_cache
    assert test_cache["last_bill"] == {}

    # generate test usage records for testing purposes,
    # including an extra entry on either side of the
    # target billing period.
    test_usage_data = gen_mixed_usage_records(
        string_to_date(test_cache['next_bill_time']),
        cba_config,
        billing_period_only=False
    )

    # add generated usage records to cache
    for record in test_usage_data:
        add_usage_record(
            hook=cba_pm.hook,
            config=cba_config,
            record=record
        )

    test_cache = cba_pm.hook.get_cache(config=cba_config)
    assert test_cache["usage_records"] == test_usage_data

    test_csp_config = cba_pm.hook.get_csp_config(config=cba_config)
    assert test_csp_config == {}

    create_csp_config(cba_pm.hook, cba_config)

    test_csp_config = cba_pm.hook.get_csp_config(config=cba_config)
    assert 'billing_api_access_ok' in test_csp_config
    assert test_csp_config['billing_api_access_ok'] is True
    assert 'timestamp' in test_csp_config
    assert 'expire' in test_csp_config
    assert 'errors' in test_csp_config
    assert test_csp_config['errors'] == []

    with mock.patch(
        'csp_billing_adapter.local_csp.randrange',
        return_value=0  # meter_billing will succeed
    ):
        # perform an empty metering operation, which shouldn't
        # modify usage records content, and should show that
        # billing API access is still ok.
        cache_data = cba_pm.hook.get_cache(config=cba_config)
        process_metering(
            config=cba_config,
            cache=cache_data,
            hook=cba_pm.hook,
            empty_metering=True
        )

        test_cache = cba_pm.hook.get_cache(config=cba_config)
        assert test_cache["usage_records"] == cache_data['usage_records']
        assert test_cache["last_bill"] == {}

        test_csp_config = cba_pm.hook.get_csp_config(config=cba_config)
        assert test_csp_config['billing_api_access_ok'] is True
        assert test_csp_config['errors'] == []

        # now perform a real billing update operation, which should
        # bill for usage records in the current billing period, and
        # then will update the cache to reflect the new billing period
        # and removed billed usage records
        cache_data = cba_pm.hook.get_cache(config=cba_config)
        process_metering(
            config=cba_config,
            cache=cache_data,
            hook=cba_pm.hook,
            empty_metering=False
        )

        test_cache = cba_pm.hook.get_cache(config=cba_config)
        # the extra records before and after the billing period
        # should remain.
        assert test_cache["usage_records"] == [
            test_usage_data[0],
            test_usage_data[-1]
        ]
        assert test_cache["last_bill"] != {}

        test_csp_config = cba_pm.hook.get_csp_config(config=cba_config)
        assert test_csp_config['billing_api_access_ok'] is True
        assert test_csp_config['errors'] == []
        assert 'usage' in test_csp_config
        assert 'last_billed' in test_csp_config

    with mock.patch(
        'csp_billing_adapter.local_csp.randrange',
        return_value=4  # meter_billing will fail
    ):
        # verify that a failed meter_billing() is handled correctly
        cache_data = cba_pm.hook.get_cache(config=cba_config)
        process_metering(
            config=cba_config,
            cache=cache_data,
            hook=cba_pm.hook,
            empty_metering=True
        )

        test_cache = cba_pm.hook.get_cache(config=cba_config)
        assert test_cache["usage_records"] == cache_data['usage_records']
        assert test_cache["last_bill"] == cache_data['last_bill']

        test_csp_config = cba_pm.hook.get_csp_config(config=cba_config)
        assert test_csp_config['billing_api_access_ok'] is False
        assert test_csp_config['errors'] != []
