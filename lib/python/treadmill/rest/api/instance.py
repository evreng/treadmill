"""Treadmill Instance REST api.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import flask
import flask_restplus as restplus
from flask_restplus import fields

from treadmill import exc
from treadmill import webutils
from treadmill.api.model import app as app_model
from treadmill.api.model import error as error_model


def init(api, cors, impl):
    """Configures REST handlers for instance resource."""
    namespace = webutils.namespace(
        api, __name__, 'Instance REST operations'
    )

    count_parser = api.parser()
    count_parser.add_argument('count', type=int, default=1, location='args')

    instances_resp_model = api.model('Instances', {
        'instances': fields.List(fields.String(description='Instances')),
    })

    inst_prio = api.model('InstancePriority', {
        '_id': fields.String(description='Application ID'),
        'priority': fields.Integer(description='Priority'),
    })
    bulk_update_inst_req = api.model('ReqBulkUpdateInstance', {
        'instances': fields.List(fields.Nested(inst_prio)),
    })

    bulk_del_inst_req = api.model('ReqBulkDeleteInstance', {
        'instances': fields.List(fields.String(description='Application ID')),
    })

    # Responses
    app_request_model, app_response_model = app_model.models(api)
    _erorr_id_why, error_model_resp = error_model.models(api)

    bulk_update_resp = api.clone(
        'ApplicationWithError', app_response_model, error_model_resp)

    app_prio = api.clone(
        'AppInstance', app_response_model, {
            'priority': fields.Integer(description='Priority'),
        }
    )

    update_resp = api.model('UpdateInstance', {
        'instances': fields.List(fields.Nested(bulk_update_resp)),
    })

    prio_request_model = api.model('ReqInstancePriority', {
        'priority': fields.List(fields.Integer(description='Priority')),
    })

    match_parser = api.parser()
    match_parser.add_argument('match', help='A glob match on an app name',
                              location='args', required=False,)

    @namespace.route(
        '/',
    )
    class _InstanceList(restplus.Resource):
        """Treadmill Instance resource"""

        @webutils.get_api(api, cors,
                          marshal=api.marshal_with,
                          resp_model=instances_resp_model,
                          parser=match_parser)
        def get(self):
            """Returns list of configured applications."""
            args = match_parser.parse_args()
            return dict(instances=impl.list(args.get('match')))

    @namespace.route(
        '/_bulk/delete',
    )
    class _InstanceBulkDelete(restplus.Resource):
        """Treadmill Instance resource"""

        @webutils.post_api(api, cors,
                           req_model=bulk_del_inst_req,)
        def post(self):
            """Bulk deletes list of instances."""
            user = flask.g.get('user')
            instance_ids = flask.request.json['instances']
            impl.bulk_delete(instance_ids, user)

    @namespace.route(
        '/_bulk/update',
    )
    class _InstanceBulkUpdate(restplus.Resource):
        """Treadmill Instance resource"""

        @webutils.post_api(api, cors,
                           req_model=bulk_update_inst_req,
                           resp_model=update_resp)
        def post(self):
            """Bulk updates list of instances."""
            deltas = flask.request.json['instances']
            # if not isinstance(deltas, list):
            #     raise exc.InvalidInputError(
            #         __name__,
            #         'deltas is not a list: {}'.format(deltas)
            #     )
            # result = []
            # for delta in deltas:
            #     if not isinstance(delta, dict):
            #         raise exc.InvalidInputError(
            #             __name__,
            #             'delta is not a dict: {}'.format(deltas)
            #         )
            #     if '_id' not in delta:
            #         raise exc.InvalidInputError(
            #             __name__,
            #             'delta is missing _id attribute: {}'.format(deltas)
            #         )

            #     # rest of validation is done in API.
            #     rsrc_id = delta.get('_id')
            #     del delta['_id']
            #     try:
            #         result.append(impl.update(rsrc_id, delta))
            #     except Exception as err:  # pylint: disable=W0703
            #         result.append({'_error': {'_id': rsrc_id,
            #                                   'why': str(err)}})
            result = impl.bulk_update(deltas)
            return {'instances': result}

    @namespace.route('/<instance_id>')
    @api.doc(params={'instance_id': 'Instance ID/name'})
    class _InstanceResource(restplus.Resource):
        """Treadmill Instance resource."""

        @webutils.get_api(api, cors,
                          marshal=api.marshal_with,
                          resp_model=app_prio)
        def get(self, instance_id):
            """Return Treadmill instance configuration."""
            instance = impl.get(instance_id)
            if not instance:
                raise exc.NotFoundError(
                    'Instance does not exist: {}'.format(instance_id)
                )
            return instance

        @webutils.post_api(api, cors,
                           req_model=app_request_model,
                           resp_model=instances_resp_model,
                           parser=count_parser)
        def post(self, instance_id):
            """Creates Treadmill instance."""
            args = count_parser.parse_args()
            count = args.get('count', 1)

            user = flask.g.get('user')

            instances = impl.create(
                instance_id, flask.request.json, count, user
            )
            return {'instances': instances}

        @webutils.put_api(api, cors,
                          req_model=prio_request_model,)
        def put(self, instance_id):
            """Updates Treadmill instance configuration."""
            return impl.update(instance_id, flask.request.json)

        @webutils.delete_api(api, cors)
        def delete(self, instance_id):
            """Deletes Treadmill application."""
            user = flask.g.get('user')
            return impl.delete(instance_id, user)
