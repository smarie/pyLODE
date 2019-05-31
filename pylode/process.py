from rdflib import Graph, RDF, RDFS, OWL, Namespace
from rdflib.namespace import SKOS, DC, DCTERMS
from rdflib.term import URIRef, Literal, BNode
import pprint
from os import path
import requests
import collections
import dateutil.parser
from jinja2 import Environment, FileSystemLoader
import operator

APP_DIR = path.dirname(path.realpath(__file__))


def _expand_graph_for_pylode(g):
    # name
    for s, o in g.subject_objects(predicate=DCTERMS.title):
        g.add((s, RDFS.label, o))

    for s, o in g.subject_objects(predicate=SKOS.prefLabel):
        g.add((s, RDFS.label, o))

    # description
    for s, o in g.subject_objects(predicate=DCTERMS.description):
        g.add((s, RDFS.comment, o))

    for s, o in g.subject_objects(predicate=SKOS.definition):
        g.add((s, RDFS.comment, o))

    # property types
    for s in g.subjects(predicate=RDF.type, object=OWL.ObjectProperty):
        g.add((s, RDF.type, RDF.Property))

    for s in g.subjects(predicate=RDF.type, object=OWL.DatatypeProperty):
        g.add((s, RDF.type, RDF.Property))

    for s in g.subjects(predicate=RDF.type, object=OWL.AnnotationProperty):
        g.add((s, RDF.type, RDF.Property))

    # class types
    for s in g.subjects(predicate=RDF.type, object=OWL.Class):
        g.add((s, RDF.type, RDFS.Class))


def _make_title_from_uri(uri):
    # can't tolerate any URI faults so return None if anything is wrong

    # URIs with no path segments or ending in slash
    segments = uri.split('/')
    if len(segments[-1]) < 1:
        return None

    # URIs with only a domain - no path segments
    if len(segments) < 4:
        return None

    # URIs ending in hash
    if segments[-1].endswith('#'):
        return None

    return segments[-1].split('#')[-1] if segments[-1].split('#')[-1] != '' else segments[-1].split('#')[-2]


# makes the fragment ID for a class, property, Named Individual (any entity) based on URI or name
def _make_fid(title, uri, existing_fids):
    # does this URI already have a fid?
    existing_fid = existing_fids.get(uri)
    if existing_fid is not None:
        return existing_fid

    # no, so make one

    def _remove_non_ascii_chars(s):
        return ''.join(i for i in s if ord(i) < 128)

    # try creating an ID from label
    # lowercase, remove spaces, escape all non-ASCII chars
    if title is not None:
        fid = _remove_non_ascii_chars(title.lower().replace(' ', ''))

        # do not return fid if it's already in use
        if fid not in existing_fids.values():
            existing_fids[uri] = fid
            return fid

    # this fid is already present so generate a new one from the URI instead

    # split URI for last slash segment
    segments = uri.split('/')
    # return None for empty string - URI ends in slash
    if len(segments[-1]) < 1:
        return None

    # return None for domains, i.e. ['http:', '', '{domain}'] - no path segments
    if len(segments) < 4:
        return None

    # split out hash URIs
    # remove any training hashes
    if segments[-1].endswith('#'):
        return None

    fid = segments[-1].split('#')[-1] if segments[-1].split('#')[-1] != '' else segments[-1].split('#')[-2]
    fid = fid.lower()

    # do not return fid if it's already in use
    if fid not in existing_fids.values():
        existing_fids[uri] = fid
        return fid
    else:
        # since it's in use but we've exhausted generation options, just add 1 to existing fid name
        existing_fids[uri] = fid + '1'
        return fid + '1'  # yeah yeah, there could be more than one but unlikely


def _extract_namespaces(g):
    # get declared namespaces, keyed by URI
    ns = {}
    uris = set()
    for k, v in g.namespaces():
        ns[str(v)] = k

    # get other namespaces
    for s, p, o in g:
        # only add URI subjects (not Blank Nodes)
        if type(s) == URIRef:
            uris.add(_get_namespace_from_uri(s))

        # predicates are always URIs
        uris.add(_get_namespace_from_uri(p))

        # only add URI objects (not Literals)
        if type(o) == URIRef:
            uris.add(_get_namespace_from_uri(o))

    for uri in uris:
        if ns.get(uri) is None:
            ns[uri] = _get_curie_prefix(uri, ns)

    # invert the key/values in instances
    instances = collections.OrderedDict()
    for k, v in sorted(ns.items(), key=lambda x: x[1]):
        instances[v] = k

    return instances


def _extract_ontology_metadata(g):
    metadata = {}
    s_str = None
    creators = []
    contributors = []
    publishers = []
    for s in g.subjects(predicate=RDF.type, object=OWL.Ontology):
        s_str = str(s)  # this is the Ontology's URI
        metadata['uri'] = s_str

        for p, o in g.predicate_objects(subject=s):
            if p == RDFS.label:
                metadata['title'] = str(o)

            if p == RDFS.comment:
                metadata['description'] = str(o)

            if p == DCTERMS.created:
                metadata['created'] = dateutil.parser.parse(str(o))

            if p == DCTERMS.modified:
                metadata['modified'] = dateutil.parser.parse(str(o))

            if p == OWL.versionIRI:
                metadata['versionIRI'] = str(o)

            if p == OWL.versionInfo:
                metadata['versionInfo'] = str(o)

            # Agents - strings
            if p == DC.creator:
                creators.append(str(o))

            if p == DC.contributor:
                contributors.append(str(o))

            if p == DC.publisher:
                publishers.append(str(o))

            if p == URIRef('http://purl.org/vocab/vann/preferredNamespacePrefix'):
                metadata['preferredNamespacePrefix'] = str(o)

            if p == URIRef('http://purl.org/vocab/vann/preferredNamespaceUri'):
                metadata['preferredNamespaceUri'] = str(o)

            # Agents - URIs or BNs
            if p == DCTERMS.creator:
                if type(o) == Literal or type(o) == URIRef:  # just treat a URI as a string
                    creators.append(str(o))
                else:  # Blank Node
                    c = {}
                    for p2, o2 in g.predicate_objects(subject=o):
                        c[str(p2)] = str(o2)
                    creators.append(c)

            if p == DCTERMS.contributor:
                if type(o) == Literal or type(o) == URIRef:  # just treat a URI as a string
                    contributors.append(str(o))
                    c2 = {}
                    for p2, o2 in g.predicate_objects(subject=o):
                        c2[str(p2)] = str(o2)
                    creators.append(c2)

            if p == DCTERMS.publisher:
                if type(o) == Literal or type(o) == URIRef:  # just treat a URI as a string
                    publishers.append(str(o))
                    p = {}
                    for p2, o2 in g.predicate_objects(subject=o):
                        p[str(p2)] = str(o2)
                    creators.append(p)

        if len(creators) > 0:
            metadata['creators'] = creators

        if len(contributors) > 0:
            metadata['contributors'] = contributors

        if len(publishers) > 0:
            metadata['publishers'] = publishers

        if metadata.get('title') is None:
            raise ValueError(
                'Your ontology does not indicate any form of label or title. '
                'You must declare one of the following for your ontology: rdfs:label, dct:title, skos:prefLabel'
            )

    if s_str is None:
        raise Exception('Your RDF file does not define an ontology. '
                        'It must contains a declaration such as <...> rdf:type owl:Ontology .')

    return metadata


def _extract_properties(g, existing_fids, namespaces):
    # properties
    properties = {}
    for s in g.subjects(predicate=RDF.type, object=RDF.Property):
        s_str = str(s)
        properties[s_str] = {}

        # property type
        if (s, RDF.type, OWL.ObjectProperty) in g:
            properties[s_str]['prop_type'] = 'op'
        elif (s, RDF.type, OWL.DatatypeProperty) in g:
            properties[s_str]['prop_type'] = 'dp'
        else:
            properties[s_str]['prop_type'] = 'ap'

        properties[s_str]['title'] = None
        properties[s_str]['description'] = None
        properties[s_str]['scopeNote'] = None
        properties[s_str]['isDefinedBy'] = None

        for p, o in g.predicate_objects(subject=s):
            if p == RDFS.label:
                properties[s_str]['title'] = str(o)

            if p == RDFS.comment:
                properties[s_str]['description'] = str(o)

            if p == SKOS.scopeNote:
                properties[s_str]['scopeNote'] = str(o)

            if p == RDFS.isDefinedBy:
                properties[s_str]['isDefinedBy'] = str(o)

        # patch title from URI if we haven;t got one
        if properties[s_str]['title'] is None:
            properties[s_str]['title'] = _make_title_from_uri(s_str)

        # make fid
        properties[s_str]['fid'] = _make_fid(properties[s_str]['title'], s_str, existing_fids)

        # super properties
        supers = []
        for o in g.objects(subject=s, predicate=RDFS.subPropertyOf):
            if type(o) != BNode:
                supers.append(str(o))
        properties[s_str]['supers'] = supers

        # sub properties
        subs = []
        for o in g.subjects(predicate=RDFS.subPropertyOf, object=s):
            if type(o) != BNode:
                subs.append(str(o))
        properties[s_str]['subs'] = subs

        # domains
        domains = []
        for o in g.objects(subject=s, predicate=RDFS.domain):
            if type(o) != BNode:
                domains.append(_make_class_html(o, namespaces))  # domains that are just classes
            else:
                # domain collections (unionOf | intersectionOf
                q = '''
                    PREFIX owl:  <http://www.w3.org/2002/07/owl#>
                    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>  

                    SELECT ?col_type ?col_member
                    WHERE {{
                        <{}> rdfs:domain ?domain .  
                        ?domain owl:unionOf|owl:intersectionOf ?collection .
                        ?domain ?col_type ?collection . 
                        ?collection rdf:rest*/rdf:first ?col_member .              
                    }} 
                '''.format(s)
                collection_type = None
                collection_members = []
                for r in g.query(q):
                    collection_type = _get_curie(str(r.col_type), namespaces)
                    collection_members.append(_get_curie(str(r.col_member), namespaces))
                domains.append((collection_type, collection_members))

        properties[s_str]['domains'] = domains

        # domainIncludes
        domainIncludes = []
        for o in g.objects(subject=s, predicate=SCO.domainIncludes):
            if type(o) != BNode:
                domainIncludes.append(_make_class_html(o, namespaces))  # domainIncludes that are just classes
            else:
                # domainIncludes collections (unionOf | intersectionOf
                q = '''
                    PREFIX owl:  <http://www.w3.org/2002/07/owl#>
                    PREFIX sco: <https://schema.org/>  

                    SELECT ?col_type ?col_member
                    WHERE {{
                        <{}> sco:domainIncludes ?domainIncludes .  
                        ?domainIncludes owl:unionOf|owl:intersectionOf ?collection .
                        ?domainIncludes ?col_type ?collection . 
                        ?collection rdf:rest*/rdf:first ?col_member .              
                    }} 
                '''.format(s)
                collection_type = None
                collection_members = []
                for r in g.query(q):
                    collection_type = _get_curie(str(r.col_type), namespaces)
                    collection_members.append(_get_curie(str(r.col_member), namespaces))
                domainIncludes.append((collection_type, collection_members))

        properties[s_str]['domainIncludes'] = domainIncludes

        # ranges
        ranges = []
        for o in g.objects(subject=s, predicate=RDFS.range):
            if type(o) != BNode:
                ranges.append(_make_class_html(o, namespaces))  # ranges that are just classes
            else:
                # range collections (unionOf | intersectionOf
                q = '''
                    PREFIX owl:  <http://www.w3.org/2002/07/owl#>
                    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>  

                    SELECT ?col_type ?col_member
                    WHERE {{
                        <{}> rdfs:range ?range .  
                        ?range owl:unionOf|owl:intersectionOf ?collection .
                        ?range ?col_type ?collection . 
                        ?collection rdf:rest*/rdf:first ?col_member .              
                    }} 
                '''.format(s)
                collection_type = None
                collection_members = []
                for r in g.query(q):
                    collection_type = _get_curie(str(r.col_type), namespaces)
                    collection_members.append(_get_curie(str(r.col_member), namespaces))
                ranges.append((collection_type, collection_members))

        properties[s_str]['ranges'] = ranges

        # rangeIncludes
        rangeIncludes = []
        for o in g.objects(subject=s, predicate=SCO.rangeIncludes):
            if type(o) != BNode:
                rangeIncludes.append(_make_class_html(o, namespaces))  # rangeIncludes that are just classes
            else:
                # rangeIncludes collections (unionOf | intersectionOf
                q = '''
                    PREFIX owl:  <http://www.w3.org/2002/07/owl#>
                    PREFIX sco: <https://schema.org/>  

                    SELECT ?col_type ?col_member
                    WHERE {{
                        <{}> sco:rangeIncludes ?rangeIncludes .  
                        ?rangeIncludes owl:unionOf|owl:intersectionOf ?collection .
                        ?rangeIncludes ?col_type ?collection . 
                        ?collection rdf:rest*/rdf:first ?col_member .              
                    }} 
                '''.format(s)
                collection_type = None
                collection_members = []
                for r in g.query(q):
                    collection_type = _get_curie(str(r.col_type), namespaces)
                    collection_members.append(_get_curie(str(r.col_member), namespaces))
                rangeIncludes.append((collection_type, collection_members))

        properties[s_str]['rangeIncludes'] = rangeIncludes

        # TODO: cater for sub property chains
    return properties


def _make_class_html(uri, namespaces):
    return _get_curie(str(uri), namespaces) + '<sup class="sup-c" title="class">c</sup>'


def _make_collection_class_html(g, parent_uri, o, namespaces):
    '''
      rdfs:subClassOf [
      a owl:Restriction ;
      rdfs:comment "A Site is established to sample some biome, bioregion, ecosystem, etc." ;
      owl:onProperty sosa:isSampleOf ;
      owl:someValuesFrom [
          a owl:Class ;
          owl:unionOf (
              plot-x:Environmental-system
              plot-x:Environmental-zone
            ) ;
        ] ;
    '''
    q = '''
        PREFIX owl:  <http://www.w3.org/2002/07/owl#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>  

        SELECT ?col_type ?col_member
        WHERE {{
            <{0}> ?xx _:{1} .
            _:{1} ?y ?z .
            ?z owl:unionOf|owl:intersectionOf ?collection .
            ?z ?col_type ?collection . 
            ?collection rdf:rest*/rdf:first ?col_member .              
        }} 
    '''.format(parent_uri, o)
    collection_members = []
    for r in g.query(q):
        if r.col_type == OWL.unionOf:
            j = ' or '
        elif r.col_type == OWL.intersectionOf:
            j = ' and '

        collection_members.append(_get_curie(str(r.col_member), namespaces))

    return '({})'.format(j.join(collection_members))


def _make_restriction_html(g, subject, restriction_bn, namespaces):
    prop = None
    card = None
    cls = None

    for p2, o2 in g.predicate_objects(subject=restriction_bn):
        if p2 != RDF.type:
            if p2 == OWL.onProperty:
                prop = _get_curie(str(o2), namespaces)
            elif p2 == OWL.onClass:
                if type(o2) == BNode:
                    if (o2, OWL.unionOf) in g.subject_predicates() or (o2, OWL.intersectionOf) in g.subject_predicates():
                        cls = _make_collection_class_html(g, subject, restriction_bn, namespaces)
                else:
                    cls = _get_curie(str(o2), namespaces)
            elif p2 in [
                OWL.cardinality,
                OWL.qualifiedCardinality,
                OWL.minCardinality,
                OWL.minQualifiedCardinality,
                OWL.maxCardinality,
                OWL.maxQualifiedCardinality,
            ]:
                if p2 in [OWL.minCardinality, OWL.minQualifiedCardinality]:
                    card = 'min'
                elif p2 in [OWL.maxCardinality, OWL.maxQualifiedCardinality]:
                    card = 'max'
                elif p2 in [OWL.cardinality, OWL.qualifiedCardinality]:
                    card = 'exactly'

                card = '<span class="cardinality">{}</span> {}'.format(card, str(o2))
            elif p2 in [OWL.allValuesFrom, OWL.someValuesFrom]:
                if p2 == OWL.allValuesFrom:
                    card = 'only'
                else:  # p2 == OWL.someValuesFrom
                    card = 'some'

                if type(o2) == BNode:
                    c = _make_collection_class_html(g, subject, restriction_bn, namespaces)
                else:
                    c = str(_get_curie(str(o2), namespaces))
                card = '<span class="cardinality">{}</span> {}'.format(card, c)

    restriction = prop + ' ' + card if card is not None else prop
    restriction = restriction + ' ' + cls if cls is not None else restriction
    return restriction


def _extract_classes(g, existing_fids, namespaces):
    classes = {}
    for s in g.subjects(predicate=RDF.type, object=RDFS.Class):
        # ignore blank nodes for things like [ owl:unionOf ( ... ) ]
        if type(s) == BNode:
            pass
        else:
            # create Python dict for class
            s_str = str(s)
            classes[s_str] = {}

            # basic class properties
            classes[s_str]['title'] = None
            classes[s_str]['description'] = None
            classes[s_str]['scopeNote'] = None
            classes[s_str]['isDefinedBy'] = None

            for p, o in g.predicate_objects(subject=s):
                if p == RDFS.label:
                    classes[s_str]['title'] = str(o)

                if p == RDFS.comment:
                    classes[s_str]['description'] = str(o)

                if p == SKOS.scopeNote:
                    classes[s_str]['scopeNote'] = str(o)

                if p == RDFS.isDefinedBy:
                    classes[s_str]['isDefinedBy'] = str(o)

            # patch title from URI if we haven;t got one
            if classes[s_str]['title'] is None:
                classes[s_str]['title'] = _make_title_from_uri(s_str)

            # make fid
            classes[s_str]['fid'] = _make_fid(classes[s_str]['title'], s_str, existing_fids)

            # equivalent classes
            equivalentClasses = []
            for o in g.objects(subject=s, predicate=OWL.equivalentClass):
                if type(o) != BNode:
                    equivalentClasses.append(_get_curie(str(o), namespaces))  # ranges that are just classes
                else:
                    # equivalent classes collections (unionOf | intersectionOf
                    q = '''
                        PREFIX owl:  <http://www.w3.org/2002/07/owl#>
                        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>  

                        SELECT ?col_type ?col_member
                        WHERE {{
                            <{}> owl:equivalentClass ?eq .  
                            ?eq owl:unionOf|owl:intersectionOf ?collection .
                            ?eq ?col_type ?collection . 
                            ?collection rdf:rest*/rdf:first ?col_member .              
                        }} 
                    '''.format(s)
                    collection_type = None
                    collection_members = []
                    for r in g.query(q):
                        collection_type = _get_curie(str(r.col_type), namespaces)
                        collection_members.append(_get_curie(str(r.col_member), namespaces))
                    equivalentClasses.append((collection_type, collection_members))
            classes[s_str]['equivalentClasses'] = equivalentClasses

            # super classes & restrictions
            supers = []
            restrictions = []
            for o in g.objects(subject=s, predicate=RDFS.subClassOf):
                if type(o) != BNode:
                    # TODO: replace all _get_curie with _make_class_html
                    supers.append(_make_class_html(o, namespaces))  # supers that are just classes
                else:  # we have a Blank Node
                    if (o, OWL.unionOf) in g.subject_predicates() or (o, OWL.intersectionOf) in g.subject_predicates():
                        supers.append(_make_collection_class_html(g, s, o, namespaces))
                    elif (o, RDF.type, OWL.Restriction) in g:  # this o is a Restriction
                        restrictions.append(_make_restriction_html(g, s, o, namespaces))

            classes[s_str]['supers'] = supers
            classes[s_str]['restrictions'] = restrictions

            # sub classes
            subs = []
            for o in g.subjects(predicate=RDFS.subClassOf, object=s):
                if type(o) != BNode:
                    subs.append(_make_class_html(o, namespaces))
                else:
                    # sub classes collections (unionOf | intersectionOf
                    q = '''
                        PREFIX owl:  <http://www.w3.org/2002/07/owl#>
                        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>  

                        SELECT ?col_type ?col_member
                        WHERE {{
                            ?sub rdfs:subClassOf <{}> . 
                            ?sub owl:unionOf|owl:intersectionOf ?collection .
                            ?sub ?col_type ?collection . 
                            ?collection rdf:rest*/rdf:first ?col_member .              
                        }} 
                    '''.format(s)
                    collection_type = None
                    collection_members = []
                    for r in g.query(q):
                        collection_type = _get_curie(str(r.col_type), namespaces)
                        collection_members.append(_get_curie(str(r.col_member), namespaces))
                    subs.append((collection_type, collection_members))
            classes[s_str]['subs'] = subs

            # TODO: cater for Named Individuals of this class - "has members"

    return classes


def _make_classes_html(classes):
    template_dir = path.join(path.dirname(path.realpath(__file__)), 'templates')
    class_template = Environment(loader=FileSystemLoader(template_dir)).get_template('class.html')
    classes_htmls = []
    for k, v in classes.items():
        classes_htmls.append(
            class_template.render(
                    uri=k,
                    fid=v['fid'],
                    title=v['title'],
                    description=v['description'],
                    supers=v['supers'],
                    restrictions=v['restrictions'],
                    subs=v['subs'],
            )
        )

    classes_template = Environment(loader=FileSystemLoader(template_dir)).get_template('classes.html')
    fids = sorted([(v.get('fid'), v.get('title')) for k, v in classes.items()], key=lambda tup: tup[1])
    classes_html = classes_template.render(
        fids=fids,
        classes=classes_htmls,
    )

    return classes_html


def _get_curie_prefix(uri, ns):
    ns_count = 0

    from pylode import CURIES, EXTRA_CURIES

    # TODO: replace this with a once-per run update CURIES function
    def get_curie_online(uri):
        try:
            r = requests.get(
                'http://prefix.cc/reverse',
                params={
                    'uri': uri,
                    'format': 'txt'
                }
            )
            if r.status_code == 200:
                return r.text.split('\t')[0]
            else:
                return None
        except requests.exceptions.ConnectionError:
            # presumably this module can't access the internet or prefix.cc is down
            return None

    def get_curie_from_namespace(uri, ns_count):
        # strip off trailing hash or slash and return last path segment
        c = uri.rstrip('#/').split('/')[-1]

        # prevent CURIE collision = return nsX (x int) if we already have this one
        for k, v in ns.items():
            if c == v:
                ns_count += 1
                return 'ns' + str(ns_count)

        return c

    # attempt to look up the well-known curie for this Namespace in http://prefix.cc dump
    for k, v in CURIES.items():
        if v == uri:
            return k

    for k, v in EXTRA_CURIES.items():
        if v == uri:
            return k

    # attempt to look up the well-known CURIE for this Namespace using http://prefix.cc online (more up-to-date)
    c = get_curie_online(uri)
    if c is not None:
        return c

    # can't fund CURIE online so make up one
    c = get_curie_from_namespace(uri, ns_count)
    return c if c is not None else ''


def _get_curie(uri, ns):
    n = _get_namespace_from_uri(uri)
    for k, v in ns.items():
        if v == n:
            return '{}:{}'.format(k, _get_uri_id(uri))

    # if no match, return the original URI
    return uri


def _get_namespace_from_uri(uri):
    # split on hash
    segments = uri.split('#')
    if len(segments) == 2:
        return segments[0] + '#'
    else:
        segments = uri.split('/')
        if len(segments) > 1:
            return '/'.join(segments[0:-1]) + '/'
        else:
            return None


def _get_uri_id(uri):
    # split on hash
    segments = uri.split('#')
    if len(segments) == 2:
        return segments[1]
    else:
        return uri.split('/')[-1]  # could return None if URI ends in /


def _get_default_namespace(g, ns, metadata):
    # if this ontology declares a preferred URI, use that
    if metadata.get('preferredNamespaceUri'):
        return metadata.get('preferredNamespaceUri')

    # ... or try a namespace declared with prefix ''
    for k, v in ns.items():
        if k == '':
            return v

    # not using - erroneous
    # # if it doesn't declare a preferredNamespaceUri but does declare a versionIRI, use that
    # if metadata.get('versionIRI'):
    #     return metadata.get('versionIRI')

    # finally try the URI of the ontology compared to all prefixes
    ontology_uri = None
    for s in g.subjects(predicate=RDF.type, object=OWL.Ontology):
        ontology_uri = str(s)

    for v in ns.values():
        if v.startswith(ontology_uri):
            return v


if __name__ == '__main__':
    g = Graph().parse(APP_DIR + '/examples/plot.ttl', format='turtle')
    _expand_graph_for_pylode(g)

    SCO = Namespace('https://schema.org')
    g.bind('sco', SCO)

    # print(g.serialize(format='turtle').decode('utf-8'))

    existing_fids = {}

    # do namespaces first so we can use then to CURIE-ise metadata
    namespaces = _extract_namespaces(g)

    # imports

    metadata = _extract_ontology_metadata(g)
    pprint.pprint(metadata)

    default_namespace = _get_default_namespace(g, namespaces, metadata)
    # pprint.pprint(default_namespace)

    properties = _extract_properties(g, existing_fids, namespaces)
    # pprint.pprint(properties)

    classes = _extract_classes(g, existing_fids, namespaces)


    with open('out.html', 'w') as f:
        f.write(_make_classes_html(classes))

    # replace all this-ont URIs with : CURIE using this ont's URI in metadata object

    # replace all domain/range, super/sub etc. class & property URIs with any CURIEs from namepaces



# for k, v in properties.items():
#     print(k, v.property_type, [e.uri for e in v.super_properties])
#
#
# for k, v in properties.items():
#     print(k, v.name if v.name is not None else None)

# for k, v in properties.items():
#     print(k, v.name if hasattr(v, 'name') else None)
# # # classes
# # classes = {}
# # for s, p, o in g.subjects(predicate=RDF.type, object=RDFS.Class):
# #     classes[s] = OwlClass(g, s, existing_fids)


# # cardinalities
# q = '''
#     PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
#     PREFIX owl: <http://www.w3.org/2002/07/owl#>
#
#     SELECT ?sub
#     WHERE {{
#         <{0}> rdfs:subPropertyOf owl:Restriction .
#         <{0}> rdfs:subPropertyOf ?r .
#         ?r owl:hasValue | owl:cardinality | owl:qualifiedCardinality | owl:maxCardinality | owl:maxQualifiedCardinality | owl:minCardinality | owl:minQualifiedCardinality ?card .
#
#     }}
# '''.format(s)
# subs = []
# for r in g.query(q):
#     if r.sub is not None:
#         subs.append(str(r.sub))
#
# classes[s]['restrictions'] = subs


