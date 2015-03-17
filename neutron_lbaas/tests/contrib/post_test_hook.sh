#!/bin/bash

set -xe

NEUTRON_LBAAS_DIR="$BASE/new/neutron-lbaas"
TEMPEST_DIR="$BASE/new/tempest"
SCRIPTS_DIR="/usr/local/jenkins/slave_scripts"

venv=${1:-"tempest"}

function generate_testr_results {
    # Give job user rights to access tox logs
    sudo -H -u $owner chmod o+rw .
    sudo -H -u $owner chmod o+rw -R .testrepository
    if [ -f ".testrepository/0" ] ; then
        .tox/$venv/bin/subunit-1to2 < .testrepository/0 > ./testrepository.subunit
        .tox/$venv/bin/python $SCRIPTS_DIR/subunit2html.py ./testrepository.subunit testr_results.html
        gzip -9 ./testrepository.subunit
        gzip -9 ./testr_results.html
        sudo mv ./*.gz /opt/stack/logs/
    fi
}

if [ "$venv" == "tempest" ]; then
    owner=tempest
    # Configure the api tests to use the tempest.conf set by devstack.
    sudo cp $TEMPEST_DIR/etc/tempest.conf $NEUTRON_LBAAS_DIR/neutron_lbaas/tests/tempest/etc
fi

# Set owner permissions according to job's requirements.
cd $NEUTRON_LBAAS_DIR
sudo chown -R $owner:stack $NEUTRON_LBAAS_DIR

# Run tests
echo "Running neutron lbaas $venv test suite"
set +e
sudo -H -u $owner $sudo_env tox -e $venv
testr_exit_code=$?
set -e

# Collect and parse results
generate_testr_results
exit $testr_exit_code
