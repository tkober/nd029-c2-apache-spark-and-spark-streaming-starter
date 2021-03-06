from pyspark.sql import SparkSession
from pyspark.sql.functions import from_json, to_json, col, unbase64, base64, split, expr
from constants import REDIS_SERVER_TOPIC, KAFKA_BROKERS_STRING
from schemas import redisSchema, customerSchema

# TODO: create a StructType for the Kafka redis-server topic which has all changes made to Redis - before Spark 3.0.0, schema inference is not automatic
# TODO: create a StructType for the Customer JSON that comes from Redis- before Spark 3.0.0, schema inference is not automatic
# TODO: create a StructType for the Kafka stedi-events topic which has the Customer Risk JSON that comes from Redis- before Spark 3.0.0, schema inference is not automatic

# See schemas.py

# TODO: create a spark application object
spark = SparkSession.builder.appName('STEDI-redis').getOrCreate()

# TODO: set the spark log level to WARN
spark.sparkContext.setLogLevel('WARN')

# TODO: using the spark application object, read a streaming dataframe from the Kafka topic redis-server as the source
# Be sure to specify the option that reads all the events from the topic including those that were published before you started the spark stream
redisServerRawDf = spark.readStream \
    .format('kafka') \
    .option('subscribe', REDIS_SERVER_TOPIC) \
    .option('kafka.bootstrap.servers', KAFKA_BROKERS_STRING) \
    .option('startingOffsets', 'earliest') \
    .load()

# TODO: cast the value column in the streaming dataframe as a STRING
redisServerDf = redisServerRawDf.selectExpr('cast(value as string) value')

# TODO:; parse the single column "value" with a json object in it, like this:
# +------------+
# | value      |
# +------------+
# |{"key":"Q3..|
# +------------+
#
# with this JSON format: {"key":"Q3VzdG9tZXI=",
# "existType":"NONE",
# "Ch":false,
# "Incr":false,
# "zSetEntries":[{
# "element":"eyJjdXN0b21lck5hbWUiOiJTYW0gVGVzdCIsImVtYWlsIjoic2FtLnRlc3RAdGVzdC5jb20iLCJwaG9uZSI6IjgwMTU1NTEyMTIiLCJiaXJ0aERheSI6IjIwMDEtMDEtMDMifQ==",
# "Score":0.0
# }],
# "zsetEntries":[{
# "element":"eyJjdXN0b21lck5hbWUiOiJTYW0gVGVzdCIsImVtYWlsIjoic2FtLnRlc3RAdGVzdC5jb20iLCJwaG9uZSI6IjgwMTU1NTEyMTIiLCJiaXJ0aERheSI6IjIwMDEtMDEtMDMifQ==",
# "score":0.0
# }]
# }
# 
# (Note: The Redis Source for Kafka has redundant fields zSetEntries and zsetentries, only one should be parsed)
#
# and create separated fields like this:
# +------------+-----+-----------+------------+---------+-----+-----+-----------------+
# |         key|value|expiredType|expiredValue|existType|   ch| incr|      zSetEntries|
# +------------+-----+-----------+------------+---------+-----+-----+-----------------+
# |U29ydGVkU2V0| null|       null|        null|     NONE|false|false|[[dGVzdDI=, 0.0]]|
# +------------+-----+-----------+------------+---------+-----+-----+-----------------+
#
# storing them in a temporary view called RedisSortedSet
redisServerDf \
    .withColumn('value', from_json('value', redisSchema)) \
    .select(col('value.*')) \
    .createOrReplaceTempView('RedisSortedSet')

# TODO: execute a sql statement against a temporary view, which statement takes the element field from the 0th element in the array of structs and create a column called encodedCustomer
# the reason we do it this way is that the syntax available select against a view is different than a dataframe, and it makes it easy to select the nth element of an array in a sql column
zSetEntriesDf = spark.sql('SELECT zSetEntries[0].element as encodedCustomer FROM RedisSortedSet')

# TODO: take the encodedCustomer column which is base64 encoded at first like this:
# +--------------------+
# |            customer|
# +--------------------+
# |[7B 22 73 74 61 7...|
# +--------------------+

# and convert it to clear json like this:
# +--------------------+
# |            customer|
# +--------------------+
# |{"customerName":"...|
# +--------------------+
#
# with this JSON format: {"customerName":"Sam Test","email":"sam.test@test.com","phone":"8015551212","birthDay":"2001-01-03"}
customerDf = zSetEntriesDf.withColumn('customer', unbase64(zSetEntriesDf.encodedCustomer).cast('string'))

# TODO: parse the JSON in the Customer record and store in a temporary view called CustomerRecords
customerDf \
    .withColumn('customer', from_json('customer', customerSchema)) \
    .select(col('customer.*')) \
    .createOrReplaceTempView('CustomerRecords')

# TODO: JSON parsing will set non-existent fields to null, so let's select just the fields we want, where they are not null as a new dataframe called emailAndBirthDayStreamingDF
emailAndBirthDayStreamingDf = spark.sql(
    'SELECT * FROM CustomerRecords WHERE email IS NOT NULL AND birthDay IS NOT NULL')

# TODO: from the emailAndBirthDayStreamingDF dataframe select the email and the birth year (using the split function)
# TODO: Split the birth year as a separate field from the birthday
# TODO: Select only the birth year and email fields as a new streaming data frame called emailAndBirthYearStreamingDF
emailAndBirthYearStreamingDf = emailAndBirthDayStreamingDf.select(
    'email',
    split(emailAndBirthDayStreamingDf.birthDay, '-').getItem(0).alias('birthYear')
)

# TODO: sink the emailAndBirthYearStreamingDF dataframe to the console in append mode
# 
# The output should look like this:
# +--------------------+-----               
# | email         |birthYear|
# +--------------------+-----
# |Gail.Spencer@test...|1963|
# |Craig.Lincoln@tes...|1962|
# |  Edward.Wu@test.com|1961|
# |Santosh.Phillips@...|1960|
# |Sarah.Lincoln@tes...|1959|
# |Sean.Howard@test.com|1958|
# |Sarah.Clark@test.com|1957|
# +--------------------+-----

# Run the python script by running the command from the terminal:
# /home/workspace/submit-redis-kafka-streaming.sh
# Verify the data looks correct
emailAndBirthYearStreamingDf \
    .writeStream \
    .outputMode('append') \
    .format('console') \
    .start() \
    .awaitTermination()
