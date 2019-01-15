# -*- coding: utf-8 -*-

"""Main module."""

# package(s) related to time, space and id
import json
import logging
import uuid

# you need these dependencies (you can get these from anaconda)
# package(s) related to the simulation
import simpy
import networkx as nx

# spatial libraries
import pyproj
import shapely.geometry

logger = logging.getLogger(__name__)


class SimpyObject:
    """General object which can be extended by any class requiring a simpy environment

    env: a simpy Environment
    """
    def __init__(self, env, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.env = env


class Identifiable:
    """Something that has a name and id

    name: a name
    id: a unique id generated with uuid"""

    def __init__(self, name, id=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        """Initialization"""
        self.name = name
        # generate some id, in this case based on m
        self.id = id if id else str(uuid.uuid1())


class Locatable:
    """Something with a geometry (geojson format)

    geometry: can be a point as well as a polygon"""

    def __init__(self, geometry, *args, **kwargs):
        super().__init__(*args, **kwargs)
        """Initialization"""
        self.geometry = geometry


class Neighbours:
    """Can be added to a locatable object (list)
    
    travel_to: list of locatables to which can be travelled"""

    def ___init(self, travel_to, *args, **kwargs):
        super().__init__(*args, **kwargs)
        """Initialization"""
        self.neighbours = travel_to


class HasContainer(SimpyObject):
    """Container class

    capacity: amount the container can hold
    level: amount the container holds initially
    container: a simpy object that can hold stuff"""

    def __init__(self, capacity, level=0, total_requested=0, *args, **kwargs):
        super().__init__(*args, **kwargs)
        """Initialization"""
        self.container = simpy.Container(self.env, capacity, init=level)
        self.total_requested = total_requested


class HasFuel(SimpyObject):
    """
    fuel_capacity: amount of fuel that the container can hold
    fuel_level: amount the container holds initially
    fuel_container: a simpy object that can hold stuff
    refuel_method: method of refueling (bunker or returning to quay) or ignore for not tracking
    """

    def __init__(self, fuel_use_loading, fuel_use_unloading, fuel_use_sailing, 
                 fuel_capacity, fuel_level, refuel_method="ignore", *args, **kwargs):
        super().__init__(*args, **kwargs)
        """Initialization"""
        self.fuel_use_loading = fuel_use_loading
        self.fuel_use_unloading = fuel_use_unloading
        self.fuel_use_sailing = fuel_use_sailing
        self.fuel_container = simpy.Container(self.env, fuel_capacity, init=fuel_level)
        self.refuel_method = refuel_method

    def consume(self, amount):
        """consume an amount of fuel"""
        
        self.log_entry("fuel consumed", self.env.now, amount)
        self.fuel_container.get(amount)

    def fill(self, fuel_delivery_rate=1):
        """fill 'er up"""

        amount = self.fuel_container.capacity - self.fuel_container.level
        if 0 < amount:
            self.fuel_container.put(amount)

        if self.refuel_method == "ignore":
            return 0
        else:
            return amount / fuel_delivery_rate
    
    def check_fuel(self, fuel_use):
        if self.fuel_container.level < fuel_use:
            #latest_log = [self.log[-1], self.t[-1], self.value[-1]]
            #del self.log[-1], self.t[-1], self.value[-1]

            refuel_duration = self.fill()

            if refuel_duration != 0:
                self.log_entry("fuel loading start", self.env.now, self.fuel_container.level)
                yield self.env.timeout(refuel_duration)
                self.log_entry("fuel loading stop", self.env.now, self.fuel_container.level)

            #self.log_entry(latest_log[0], self.env.now, latest_log[2])


class Routeable:
    """Something with a route (networkx format)
    route: a networkx path"""

    def __init__(self, route, *args, **kwargs):
        super().__init__(*args, **kwargs)
        """Initialization"""
        self.route = route


class Movable(SimpyObject, Locatable, Routeable):
    """Movable class

    Used for object that can move with a fixed speed
    geometry: point used to track its current location
    v: speed"""

    def __init__(self, v=1, *args, **kwargs):
        super().__init__(*args, **kwargs)
        """Initialization"""
        self.v = v
        self.wgs84 = pyproj.Geod(ellps='WGS84')

    def move(self):
        """determine distance between origin and destination, and
        yield the time it takes to travel it
        
        Assumption is that self.path is in the right order - vessel moves from route[0] to route[-1].
        """
        self.distance = 0

        # Check if vessel is at correct location - if note, move to location
        if self.geometry != nx.get_node_attributes(self.env.FG.graph, "Geometry")[self.route[0]]:
            orig = self.geometry
            dest = nx.get_node_attributes(self.env.FG.graph, "Geometry")[self.route[0]]
            
            self.distance += self.wgs84.inv(shapely.geometry.asShape(orig).x, shapely.geometry.asShape(orig).y, 
                                            shapely.geometry.asShape(dest).x, shapely.geometry.asShape(dest).y)[2]
    
            yield self.env.timeout(self.distance / self.current_speed)
            self.log_entry("Sailing to start", self.env.now, self.distance, dest)

        # Move over the path and log every step
        for node in enumerate(self.route):
            origin = self.route[node[0]]
            destination = self.route[node[0] + 1]

            yield from self.pass_edge(origin, destination)

            if node[0] + 2 == len(self.route):
                break

        # check for sufficient fuel
        if isinstance(self, HasFuel):
            fuel_consumed = self.fuel_use_sailing(self.distance, self.current_speed)
            self.check_fuel(fuel_consumed)

        self.geometry = nx.get_node_attributes(self.env.FG.graph, "Geometry")[destination]
        logger.debug('  distance: ' + '%4.2f' % self.distance + ' m')
        logger.debug('  sailing:  ' + '%4.2f' % self.current_speed + ' m/s')
        logger.debug('  duration: ' + '%4.2f' % ((self.distance / self.current_speed) / 3600) + ' hrs')

        # lower the fuel
        if isinstance(self, HasFuel):
            # remove seconds of fuel
            self.consume(fuel_consumed)
    
    def pass_edge(self, origin, destination):
        edge = self.env.FG.graph.edges[origin, destination]
        orig = nx.get_node_attributes(self.env.FG.graph, "Geometry")[origin]
        dest = nx.get_node_attributes(self.env.FG.graph, "Geometry")[destination]

        distance = self.wgs84.inv(shapely.geometry.asShape(orig).x, shapely.geometry.asShape(orig).y, 
                                  shapely.geometry.asShape(dest).x, shapely.geometry.asShape(dest).y)[2]

        self.distance += distance
        arrival = self.env.now
        
        if "Object" in edge.keys() and "Resources" in edge.keys():
            with self.env.FG.graph.edges[origin, destination]["Resources"].request() as request:
                yield request

                if edge["Object"] == "Lock":
                    self.pass_lock(origin, destination)
                else:
                    if arrival != self.env.now:
                        yield self.env.timeout(self.env.now - arrival)
                        self.log_entry("Waiting to pass edge {} - {}".format(origin, destination), self.env.now, 0, orig)    
        
                    self.env.timeout(distance / self.current_speed)
                    self.log_entry("Sailing from node {} to node {}".format(origin, destination), self.env.now, distance, dest)
        
        # Act based on resources
        elif "Resources" in edge.keys():
            with self.env.FG.graph.edges[origin, destination]["Resources"].request() as request:
                yield request

                if arrival != self.env.now:
                    yield self.env.timeout(self.env.now - arrival)
                    self.log_entry("Waiting to pass edge {} - {}".format(origin, destination), self.env.now, 0, orig)    

                yield self.env.timeout(distance / self.current_speed)
                self.log_entry("Sailing from node {} to node {}".format(origin, destination), self.env.now, distance, dest)
        
        else:
            yield self.env.timeout(distance / self.current_speed)
            self.log_entry("Sailing from node {} to node {}".format(origin, destination), self.env.now, distance, dest)
        

    def pass_lock(self, origin, destination):
        edge = self.graph.edges[origin, destination]
        water_level = origin
        
        # Check direction 
        if edge["Water level"]:
            
            # If water level at origin is not similar to lock-water level --> change water level and wait
            if water_level != edge["Water level"]:
                
                # Doors closing
                passer.log_entry("Doors closing", passer.env.now, 0, origin)
                passer.env.timeout(10 * 60)

                # Converting chamber
                passer.log_entry("Converting chamber", passer.env.now, 0, origin)
                passer.env.timeout(20 * 60)

                # Doors opening
                passer.log_entry("Doors opening", passer.env.now, 0, origin)
                passer.env.timeout(10 * 60)

                # Change edge water level
                self.graph.edges[origin, destination]["Water level"] = water_level

        # If direction is similar to lock-water level --> pass the lock
        if not edge["Water level"] or edge["Water level"] == water_level:
            
            # Doors closing
            passer.log_entry("Sailing into lock", passer.env.now, 0, origin)
            passer.env.timeout(5 * 60)

            # Converting chamber
            passer.log_entry("Converting chamber", passer.env.now, 0, origin)
            passer.env.timeout(20 * 60)

            # Doors opening
            passer.log_entry("Sailing out of lock", passer.env.now, 0, origin)
            passer.env.timeout(5 * 60)

            # Change edge water level
            self.graph.edges[origin, destination]["Water level"] = destination

    def is_at(self, locatable, tolerance=100):
        current_location = shapely.geometry.asShape(self.geometry)
        other_location = shapely.geometry.asShape(locatable.geometry)
        _, _, distance = self.wgs84.inv(current_location.x, current_location.y,
                                        other_location.x, other_location.y)
        return distance < tolerance

    @property
    def current_speed(self):
        return self.v


class ContainerDependentMovable(Movable, HasContainer):
    """ContainerDependentMovable class

    Used for objects that move with a speed dependent on the container level
    compute_v: a function, given the fraction the container is filled (in [0,1]), returns the current speed"""

    def __init__(self,
                 compute_v,
                 *args, **kwargs):
        super().__init__(*args, **kwargs)
        """Initialization"""
        self.compute_v = compute_v
        self.wgs84 = pyproj.Geod(ellps='WGS84')

    @property
    def current_speed(self):
        return self.compute_v(self.container.level / self.container.capacity)


class HasResource(SimpyObject):
    """HasProcessingLimit class

    Adds a limited Simpy resource which should be requested before the object is used for processing."""

    def __init__(self, nr_resources=1, *args, **kwargs):
        super().__init__(*args, **kwargs)
        """Initialization"""
        self.resource = simpy.Resource(self.env, capacity=nr_resources)


class Log(SimpyObject):
    """Log class

    log: log message [format: 'start activity' or 'stop activity']
    t: timestamp
    value: a value can be logged as well
    geometry: value from locatable (lat, lon)"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        """Initialization"""
        self.log = {"Message": [],
                    "Timestamp": [],
                    "Value": [],
                    "Geometry": []}

    def log_entry(self, log, t, value, geometry_log):
        """Log"""
        self.log["Message"].append(log)
        self.log["Timestamp"].append(t)
        self.log["Value"].append(value)
        self.log["Geometry"].append(geometry_log)

    def get_log_as_json(self):
        json = []
        for msg, t, value, geometry_log in zip(self.log["Message"], self.log["Timestamp"], self.log["Value"], self.log["Geometry"]):
            json.append(dict(message=msg, time=t, value=value, geometry_log=geometry_log))
        return json


class Processor(SimpyObject):
    """Processor class

    rate: rate with which quantity can be processed [amount/s]"""

    def __init__(self, rate, *args, **kwargs):
        super().__init__(*args, **kwargs)
        """Initialization"""
        self.rate = rate

    # noinspection PyUnresolvedReferences
    def process(self, origin, destination, amount, origin_resource_request=None, destination_resource_request=None):
        """get amount from origin container, put amount in destination container,
        and yield the time it takes to process it"""
        assert isinstance(origin, HasContainer) and isinstance(destination, HasContainer)
        assert isinstance(origin, HasResource) and isinstance(destination, HasResource)
        assert isinstance(origin, Log) and isinstance(destination, Log)

        assert origin.container.level >= amount
        assert destination.container.capacity - destination.container.level >= amount

        my_origin_turn = origin_resource_request
        if my_origin_turn is None:
            my_origin_turn = origin.resource.request()

        my_dest_turn = destination_resource_request
        if my_dest_turn is None:
            my_dest_turn = destination.resource.request()

        yield my_origin_turn
        yield my_dest_turn

        # check fuel from origin
        if isinstance(origin, HasFuel):
            fuel_consumed_origin = origin.fuel_use_unloading(amount)
            origin.check_fuel(fuel_consumed_origin)

        # check fuel from destination
        if isinstance(destination, HasFuel):
            fuel_consumed_destination = destination.fuel_use_unloading(amount)
            destination.check_fuel(fuel_consumed_destination)
        
        # check fuel from processor if not origin or destination  -- case of backhoe with barges
        if self.id != origin.id and self.id != destination.id and isinstance(self, HasFuel):
            # if origin is moveable -- unloading
            if isinstance(origin, Movable):
                fuel_consumed = self.fuel_use_unloading(amount)
                self.check_fuel(fuel_consumed)
            
            # if destinaion is moveable -- loading
            if isinstance(destination, Movable):
                fuel_consumed = self.fuel_use_loading(amount)
                self.check_fuel(fuel_consumed)

            # third option -- from moveable to moveable -- take highest fuel consumption
            else:
                fuel_consumed = max(self.fuel_use_unloading(amount), self.fuel_use_loading(amount))
                self.check_fuel(fuel_consumed)
                
        origin.log_entry('unloading start', self.env.now, origin.container.level)
        destination.log_entry('loading start', self.env.now, destination.container.level)

        origin.container.get(amount)
        destination.container.put(amount)
        yield self.env.timeout(amount / self.rate)

        # lower the fuel for all active entities
        if isinstance(origin, HasFuel):
            origin.consume(fuel_consumed_origin)

        if isinstance(destination, HasFuel):
            destination.consume(fuel_consumed_destination)

        if self.id != origin.id and self.id != destination.id and isinstance(self, HasFuel):
            self.consume(fuel_consumed)

        origin.log_entry('unloading stop', self.env.now, origin.container.level)
        destination.log_entry('loading stop', self.env.now, destination.container.level)

        logger.debug('  process:        ' + '%4.2f' % ((amount / self.rate) / 3600) + ' hrs')

        if origin_resource_request is None:
            origin.resource.release(my_origin_turn)
        if destination_resource_request is None:
            destination.resource.release(my_dest_turn)


class DictEncoder(json.JSONEncoder):
    """serialize a simpy digital_twin object to json"""
    def default(self, o):
        result = {}
        for key, val in o.__dict__.items():
            if isinstance(val, simpy.Environment):
                continue
            if isinstance(val, simpy.Container):
                result['capacity'] = val.capacity
                result['level'] = val.level
            elif isinstance(val, simpy.Resource):
                result['nr_resources'] = val.capacity
            else:
                result[key] = val

        return result


def serialize(obj):
    return json.dumps(obj, cls=DictEncoder)