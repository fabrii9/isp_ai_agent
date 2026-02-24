# -*- coding: utf-8 -*-
"""
IspTools — Implementación de las herramientas ISP disponibles para el agente AI.

Cada método público corresponde a una ai.tool registrada en la base de datos.
El nombre del método debe coincidir con el campo `python_method` de ai.tool.

Los métodos siempre reciben `partner` como primer kwargs y retornan un dict
que el LLM interpretará para formular la respuesta.
"""

import logging
from datetime import datetime, timedelta

_logger = logging.getLogger(__name__)


class IspTools:
    """
    Colección de tools ejecutables por el agente AI para el contexto ISP.

    Todos los métodos deben:
    - Aceptar `partner` como kwarg
    - Retornar un dict serializable a JSON
    - Manejar excepciones internamente
    - NO enviar mensajes directamente (eso lo hace el router)
    """

    def __init__(self, env):
        self.env = env

    # =========================================================================
    # FACTURACIÓN / DEUDAS
    # =========================================================================

    def execute_verify_identity(self, partner, vat=None, **kwargs) -> dict:
        """
        Verificar identidad del cliente comparando DNI/CUIT ingresado contra el
        campo 'vat' del partner.

        :param vat: DNI o CUIT ingresado por el cliente (puede tener guiones/espacios)
        :return: {"verified": bool, "partner_name": str}
        """
        if not partner:
            return {'error': True, 'message': 'Partner no identificado.'}
        if not vat:
            return {'verified': False, 'message': 'No se proporcionó DNI/CUIT.'}

        # Normalizar: solo dígitos para comparar
        def only_digits(s):
            return ''.join(c for c in str(s) if c.isdigit())

        input_digits = only_digits(vat)
        stored_vat = only_digits(partner.vat or '')

        if not stored_vat:
            return {
                'verified': False,
                'message': 'El cliente no tiene DNI/CUIT registrado en el sistema.',
            }

        verified = input_digits == stored_vat
        return {
            'verified': verified,
            'partner_name': partner.name if verified else None,
            'message': 'Identidad verificada.' if verified else 'El DNI/CUIT no coincide con nuestros registros.',
        }

    def execute_check_debt(self, partner, **kwargs) -> dict:
        """
        Consultar saldo real del partner usando las líneas de cuenta cobrable.

        Captura: facturas impagas, pagos parciales, créditos a favor (saldo negativo)
        y notas de crédito. Es el saldo real que ve contabilidad.

        :return: {"balance": float, "has_debt": bool, "has_credit": bool, "currency": str}
        """
        if not partner:
            return {'error': True, 'message': 'Partner no identificado.'}

        try:
            # Consultar líneas de cuenta cobrable no reconciliadas
            # Esto captura: facturas, notas de crédito, overpayments y créditos a favor
            lines = self.env['account.move.line'].sudo().search([
                ('partner_id', 'child_of', partner.id),
                ('account_id.account_type', '=', 'asset_receivable'),
                ('reconciled', '=', False),
                ('move_id.state', '=', 'posted'),
            ])

            # balance > 0 → cliente debe dinero
            # balance < 0 → cliente tiene crédito a favor (pagó de más)
            balance = sum(lines.mapped('amount_residual'))

            # Facturas vencidas (para dar contexto adicional)
            overdue_invoices = self.env['account.move'].sudo().search([
                ('partner_id', 'child_of', partner.id),
                ('move_type', '=', 'out_invoice'),
                ('state', '=', 'posted'),
                ('payment_state', 'in', ('not_paid', 'partial')),
                ('invoice_date_due', '<', datetime.now().date()),
            ])

            currency = self.env.company.currency_id.name

            return {
                'partner_name': partner.name,
                'balance': round(balance, 2),
                'has_debt': balance > 0,
                'has_credit': balance < 0,
                'credit_amount': round(abs(balance), 2) if balance < 0 else 0,
                'overdue_invoice_count': len(overdue_invoices),
                'currency': currency,
            }
        except Exception as e:
            _logger.error('check_debt error para partner [%s]: %s', partner.id, e)
            return {'error': True, 'message': str(e)}

    def execute_list_overdue_invoices(self, partner, limit=5, **kwargs) -> dict:
        """
        Listar facturas vencidas del partner.

        :param limit: máximo de facturas a retornar (default 5)
        :return: lista de facturas con número, fecha vencimiento, monto
        """
        if not partner:
            return {'error': True, 'message': 'Partner no identificado.'}

        try:
            invoices = self.env['account.move'].sudo().search([
                ('partner_id', 'child_of', partner.id),
                ('move_type', '=', 'out_invoice'),
                ('state', '=', 'posted'),
                ('payment_state', 'in', ('not_paid', 'partial')),
                ('invoice_date_due', '<', datetime.now().date()),
            ], order='invoice_date_due asc', limit=int(limit))

            currency = self.env.company.currency_id.name
            result = []
            for inv in invoices:
                result.append({
                    'number': inv.name,
                    'date_due': str(inv.invoice_date_due),
                    'amount_due': round(inv.amount_residual, 2),
                    'currency': currency,
                    'days_overdue': (datetime.now().date() - inv.invoice_date_due).days
                    if inv.invoice_date_due else 0,
                })

            return {
                'invoices': result,
                'count': len(result),
                'currency': currency,
            }
        except Exception as e:
            _logger.error('list_overdue_invoices error: %s', e)
            return {'error': True, 'message': str(e)}

    # =========================================================================
    # PAGOS
    # =========================================================================

    def execute_generate_payment_link(self, partner, invoice_id=None, **kwargs) -> dict:
        """
        Generar link de pago para la deuda del partner.

        Si invoice_id se especifica, genera el link para esa factura específica.
        Caso contrario, genera link para la factura vencida más antigua.

        :return: {"payment_url": str, "amount": float, "expiry": str}
        """
        if not partner:
            return {'error': True, 'message': 'Partner no identificado.'}

        try:
            # Buscar factura objetivo
            if invoice_id:
                invoice = self.env['account.move'].sudo().browse(int(invoice_id))
                if not invoice.exists() or invoice.partner_id.id != partner.id:
                    return {'error': True, 'message': 'Factura no encontrada.'}
            else:
                # Factura vencida más antigua
                invoice = self.env['account.move'].sudo().search([
                    ('partner_id', 'child_of', partner.id),
                    ('move_type', '=', 'out_invoice'),
                    ('state', '=', 'posted'),
                    ('payment_state', 'in', ('not_paid', 'partial')),
                    ('invoice_date_due', '<=', datetime.now().date()),
                ], order='invoice_date_due asc', limit=1)

                if not invoice:
                    return {
                        'error': False,
                        'message': 'El cliente no tiene facturas vencidas pendientes.',
                        'payment_url': None,
                    }

            # Intentar usar el módulo de pagos de Odoo si está disponible
            try:
                base_url = invoice.get_base_url()
                # Link de portal de pago de Odoo
                payment_url = f"{base_url}/my/invoices/{invoice.id}?access_token={invoice._portal_ensure_token()}"
            except Exception:
                # Fallback: URL genérica
                base_url = self.env['ir.config_parameter'].sudo().get_param(
                    'web.base.url', 'http://localhost:8069'
                )
                payment_url = f"{base_url}/my/invoices/{invoice.id}"

            expiry = (datetime.now() + timedelta(hours=24)).strftime('%d/%m/%Y %H:%M')
            currency = self.env.company.currency_id.name

            return {
                'payment_url': payment_url,
                'invoice_number': invoice.name,
                'amount': round(invoice.amount_residual, 2),
                'currency': currency,
                'expiry': expiry,
            }
        except Exception as e:
            _logger.error('generate_payment_link error: %s', e)
            return {'error': True, 'message': str(e)}

    # =========================================================================
    # SOPORTE / RECLAMOS
    # =========================================================================

    def execute_create_ticket(self, partner, subject=None, description=None,
                              ticket_type=None, **kwargs) -> dict:
        """
        Crear un ticket de soporte/reclamo en el módulo Helpdesk de Odoo.

        :param subject: título del ticket (obligatorio — el agente debe pedírselo al cliente)
        :param description: descripción detallada del problema
        :param ticket_type: 'technical' | 'billing' | 'general'
        :return: {"ticket_id": int, "ticket_ref": str, "team": str}
        """
        if not partner:
            return {'error': True, 'message': 'Partner no identificado.'}
        if not subject:
            return {'error': True, 'needs_info': True,
                    'message': 'Falta el asunto del reclamo. Pedíle al cliente que describa brevemente el problema.'}

        try:
            if 'helpdesk.ticket' not in self.env:
                return {'error': True, 'message': 'El módulo Help Desk no está instalado.'}

            # Mapear ticket_type a nombre de equipo preferido
            team_priority = {
                'technical': ['Soporte Técnico', 'Técnico', 'Technical', 'Soporte'],
                'billing':   ['Facturación', 'Billing', 'Administración', 'Soporte'],
                'general':   ['Soporte', 'General', 'Help Desk'],
            }
            preferred_names = team_priority.get(ticket_type, ['Soporte', 'Help Desk'])

            team = None
            for tname in preferred_names:
                team = self.env['helpdesk.team'].sudo().search(
                    [('name', 'ilike', tname)], limit=1
                )
                if team:
                    break
            if not team:
                # fallback: cualquier equipo
                team = self.env['helpdesk.team'].sudo().search([], limit=1)

            # Etiquetas opcionales según tipo
            tag_map = {
                'technical': 'técnico',
                'billing':   'facturación',
                'general':   'consulta',
            }
            tag_ids = []
            if ticket_type and ticket_type in tag_map:
                tag = self.env['helpdesk.tag'].sudo().search(
                    [('name', 'ilike', tag_map[ticket_type])], limit=1
                )
                if tag:
                    tag_ids = [(4, tag.id)]

            vals = {
                'name': subject,
                'partner_id': partner.id,
                'partner_name': partner.name,
                'partner_email': partner.email or '',
                'partner_phone': partner.phone or partner.mobile or '',
                'description': description or f'Reclamo generado vía WhatsApp por {partner.name}.',
            }
            if team:
                vals['team_id'] = team.id
            if tag_ids:
                vals['tag_ids'] = tag_ids

            ticket = self.env['helpdesk.ticket'].sudo().create(vals)

            # ticket_ref es el número auto-generado (ej: "DESK00012")
            ticket_ref = ticket.ticket_ref or str(ticket.id)

            return {
                'ticket_id': ticket.id,
                'ticket_ref': ticket_ref,
                'subject': ticket.name,
                'team': team.name if team else 'Soporte',
                'message': f'Ticket {ticket_ref} creado exitosamente. El equipo de {team.name if team else "soporte"} se pondrá en contacto.',
            }
        except Exception as e:
            _logger.error('create_ticket error: %s', e)
            return {'error': True, 'message': str(e)}

    def execute_list_open_tickets(self, partner, limit=5, **kwargs) -> dict:
        """
        Listar tickets abiertos del partner.

        :return: lista de tickets con número, título, estado
        """
        if not partner:
            return {'error': True, 'message': 'Partner no identificado.'}

        try:
            if 'helpdesk.ticket' not in self.env:
                return {'error': True, 'message': 'Módulo Help Desk no instalado.'}

            tickets = self.env['helpdesk.ticket'].sudo().search([
                ('partner_id', '=', partner.id),
                ('stage_id.is_close', '=', False),
            ], order='create_date desc', limit=int(limit))

            result = []
            for t in tickets:
                result.append({
                    'id': t.id,
                    'number': t.name,
                    'subject': t.name,
                    'stage': t.stage_id.name if t.stage_id else 'Sin etapa',
                    'created': str(t.create_date.date()) if t.create_date else '',
                })

            return {
                'tickets': result,
                'count': len(result),
            }
        except Exception as e:
            _logger.error('list_open_tickets error: %s', e)
            return {'error': True, 'message': str(e)}

    # =========================================================================
    # SERVICIOS ISP
    # =========================================================================

    def execute_service_status(self, partner, **kwargs) -> dict:
        """
        Consultar el estado de los servicios contratados por el partner.

        :return: lista de servicios con estado (activo/suspendido/etc.)
        """
        if not partner:
            return {'error': True, 'message': 'Partner no identificado.'}

        try:
            # Intentar con modelo ISP360
            if 'isp360.contract' in self.env:
                contracts = self.env['isp360.contract'].sudo().search([
                    ('partner_id', '=', partner.id),
                ])
                services = []
                for c in contracts:
                    services.append({
                        'name': c.name,
                        'state': c.state,
                        'product': c.product_id.name if c.product_id else '',
                        'address': c.street or '',
                    })
                return {
                    'partner_name': partner.name,
                    'services': services,
                    'count': len(services),
                }

            # Fallback: buscar suscripciones de Odoo
            if 'sale.subscription' in self.env:
                subs = self.env['sale.subscription'].sudo().search([
                    ('partner_id', '=', partner.id),
                    ('stage_id.in_progress', '=', True),
                ])
                services = [{'name': s.name, 'state': s.stage_id.name} for s in subs]
                return {'services': services, 'count': len(services)}

            return {'error': False, 'message': 'No se encontró información de servicios.', 'services': []}

        except Exception as e:
            _logger.error('service_status error: %s', e)
            return {'error': True, 'message': str(e)}

    def execute_suspend_service(self, partner, contract_id=None, reason=None, **kwargs) -> dict:
        """
        Suspender un servicio del partner.

        REQUIERE CONFIRMACIÓN — marcar como requires_confirmation=True en ai.tool.
        """
        if not partner:
            return {'error': True, 'message': 'Partner no identificado.'}

        try:
            if 'isp360.contract' not in self.env:
                return {'error': True, 'message': 'Módulo ISP360 no disponible.'}

            domain = [('partner_id', '=', partner.id), ('state', '=', 'active')]
            if contract_id:
                domain.append(('id', '=', int(contract_id)))

            contract = self.env['isp360.contract'].sudo().search(domain, limit=1)
            if not contract:
                return {'error': True, 'message': 'No se encontró contrato activo.'}

            contract.action_suspend()
            return {
                'message': f'Servicio {contract.name} suspendido.',
                'contract_id': contract.id,
                'new_state': 'suspended',
            }
        except Exception as e:
            _logger.error('suspend_service error: %s', e)
            return {'error': True, 'message': str(e)}

    def execute_activate_service(self, partner, contract_id=None, **kwargs) -> dict:
        """
        Activar/restaurar un servicio suspendido del partner.

        REQUIERE CONFIRMACIÓN.
        """
        if not partner:
            return {'error': True, 'message': 'Partner no identificado.'}

        try:
            if 'isp360.contract' not in self.env:
                return {'error': True, 'message': 'Módulo ISP360 no disponible.'}

            domain = [('partner_id', '=', partner.id), ('state', '=', 'suspended')]
            if contract_id:
                domain.append(('id', '=', int(contract_id)))

            contract = self.env['isp360.contract'].sudo().search(domain, limit=1)
            if not contract:
                return {'error': True, 'message': 'No se encontró contrato suspendido.'}

            contract.action_activate()
            return {
                'message': f'Servicio {contract.name} activado.',
                'contract_id': contract.id,
                'new_state': 'active',
            }
        except Exception as e:
            _logger.error('activate_service error: %s', e)
            return {'error': True, 'message': str(e)}

    # =========================================================================
    # CRM / LEADS
    # =========================================================================

    def execute_create_lead(self, partner, name=None, description=None,
                            phone=None, **kwargs) -> dict:
        """
        Crear un lead/oportunidad en el CRM.

        :return: {"lead_id": int, "lead_name": str}
        """
        try:
            if 'crm.lead' not in self.env:
                return {'error': True, 'message': 'Módulo CRM no instalado.'}

            vals = {
                'name': name or f'Lead desde WhatsApp — {partner.name if partner else phone or "Desconocido"}',
                'description': description or 'Lead generado vía WhatsApp AI Agent',
                'type': 'lead',
            }
            if partner:
                vals['partner_id'] = partner.id
                vals['partner_name'] = partner.name
                vals['email_from'] = partner.email
                vals['phone'] = partner.phone or partner.mobile
            elif phone:
                vals['phone'] = phone

            lead = self.env['crm.lead'].sudo().create(vals)
            return {
                'lead_id': lead.id,
                'lead_name': lead.name,
                'message': f'Lead #{lead.id} creado exitosamente.',
            }
        except Exception as e:
            _logger.error('create_lead error: %s', e)
            return {'error': True, 'message': str(e)}

    def execute_qualify_lead(self, partner, lead_id=None, interest_level=None,
                             notes=None, **kwargs) -> dict:
        """
        Calificar un lead existente.

        :param lead_id: ID del lead
        :param interest_level: 'hot' | 'warm' | 'cold'
        :param notes: notas adicionales
        """
        try:
            if 'crm.lead' not in self.env:
                return {'error': True, 'message': 'Módulo CRM no instalado.'}

            domain = []
            if lead_id:
                domain = [('id', '=', int(lead_id))]
            elif partner:
                domain = [('partner_id', '=', partner.id), ('type', '=', 'lead')]

            lead = self.env['crm.lead'].sudo().search(domain, limit=1, order='create_date desc')
            if not lead:
                return {'error': True, 'message': 'Lead no encontrado.'}

            priority_map = {'hot': '2', 'warm': '1', 'cold': '0'}
            update_vals = {}
            if interest_level:
                update_vals['priority'] = priority_map.get(interest_level, '1')
            if notes:
                update_vals['description'] = (lead.description or '') + f'\n[AI] {notes}'

            if update_vals:
                lead.sudo().write(update_vals)

            return {
                'lead_id': lead.id,
                'lead_name': lead.name,
                'priority': interest_level or 'unchanged',
                'message': f'Lead #{lead.id} calificado como {interest_level or "sin cambios"}.',
            }
        except Exception as e:
            _logger.error('qualify_lead error: %s', e)
            return {'error': True, 'message': str(e)}

    # =========================================================================
    # ESCALAMIENTO
    # =========================================================================

    def execute_escalate_to_human(self, partner, channel=None, reason=None, **kwargs) -> dict:
        """
        Marcar el canal para atención humana y notificar al equipo.

        :return: {"escalated": bool, "message": str}
        """
        try:
            if channel:
                # Postear notificación en el canal
                channel.sudo().message_post(
                    body=f'⚠️ Escalado a soporte humano. Motivo: {reason or "Solicitado por el cliente"}',
                    message_type='notification',
                    subtype_xmlid='mail.mt_comment',
                )

            return {
                'escalated': True,
                'message': (
                    'Tu consulta fue escalada a nuestro equipo de soporte. '
                    'Te atenderemos a la brevedad. Gracias por tu paciencia.'
                ),
            }
        except Exception as e:
            _logger.error('escalate_to_human error: %s', e)
            return {'error': True, 'message': str(e)}
